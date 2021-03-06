# pylint: disable = broad-except
import sys
import logging
import time
import json
import os
import re
import smtplib
from email.message import EmailMessage
import requests
import docker

# Set logging options and variables
logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.INFO)
polling_interval_after_restart: int = int(os.getenv('POLLING_INTERVAL_AFTER_RESTART', '600'))
polling_interval: int = int(os.getenv('POLLING_INTERVAL', '20'))
docker_host: str = os.getenv('DOCKER_HOSTMACHINE', 'UNKNOWN')
slack_webhook_url: str = os.getenv('SLACK_WEBHOOK_URL', '')
email_sender: str = os.getenv('EMAIL_SENDER', '')
email_receiver: str = os.getenv('EMAIL_RECEIVER', '')
smtp_server: str = os.getenv('SMTP_SERVER', '')
restarted_containers: list = []
notification_content: dict = {}

# Test and establish connection to docker socket
try:
    CLIENT = docker.from_env()
    CLIENT.version()
    logging.info("Connection to Docker socket OK")
except Exception as err:
    logging.fatal("%s", err)
    sys.exit()


def send_slack_message(content) -> None:
    if slack_webhook_url != "":
        try:
            requests.post(slack_webhook_url, data=json.dumps(content),
                          headers={'Content-Type': 'application/json'})
            logging.info("Message sent to Slack webhook: %s", content['text'])
        except (requests.exceptions.Timeout, ConnectionError) as err:
            logging.error("%s", err)


def send_smtp_message(content) -> None:
    if email_receiver != "" and smtp_server != "":
        email_content: str = re.sub('[^ :A-Za-z0-9]+', '', content)
        email_message = EmailMessage()
        email_message.set_content(email_content)
        email_message['Subject'] = 'Container Watchdog Alert notification'
        email_message['From'] = email_sender
        email_message['To'] = email_receiver
        mail = smtplib.SMTP(smtp_server, 25, timeout=40)
        try:
            mail.send_message(email_message)
            logging.info("Email sent to %s with content: %s", email_receiver, email_content)
        except Exception as err:
            logging.error("%s", err)
        mail.quit()


def get_container_health_status(container_object) -> str:
    try:
        health_status = container_object.attrs['State']['Health']['Status']
    except KeyError:
        health_status = 'nokey'
    return health_status

def get_container_health_log(container_object) -> str:
    try:
        health_log = container_object.attrs['State']['Health']['Log'][-1]['Output']
    except KeyError:
        health_log = 'nokey'
    except IndexError:
        health_log = 'empty'
    return health_log

def restart_container(container_object) -> None:
    try:
        container_object.restart()
        logging.info("Restarted container: %s", container_object.name)
        notification_content['text'] = ("[Container watchdog]: Container restarted\n"
                                        "\tHost: [ *_{0}_* ]\n"
                                        "\tContainer: [ *_{1}_* ]\n"
                                        "\tState: [ *_{2}_* ]\n"
                                        "\tHealthstatus: [ *_{3}_* ]\n"
                                        "\tOutput: [ _{4}_ ]".format(docker_host, container_object.name, container_status, container_health_status, container_health_log))
        if container_object.short_id not in restarted_containers:
            restarted_containers.append(container_object.short_id)
    except Exception as err:
        logging.fatal("%s", err)
        notification_content['text'] = ("[Container watchdog]: Docker daemon failed to restart container *{0}* on hostmachine *{1}*"
                                        " with error message: _{2}_".format(container_object.name, docker_host, err))


def container_recovered(container_object) -> None:
    logging.info("Container %s has recovered and is now healthy!", container_object.name)
    notification_content['text'] = ("[Container watchdog]: Container has recovered\n"
                                        "\tHost: [ *_{0}_* ]\n"
                                        "\tContainer: [ *_{1}_* ]\n"
                                        "\tState: [ *_{2}_* ]\n"
                                        "\tHealthstatus: [ *_{3}_* ]".format(docker_host, container_object.name, container_status, container_health_status))

    restarted_containers.remove(container_object.short_id)


# Run loop indefinetly polling every $POLLING_INTERVAL normally or in $POLLING_INTERVAL_AFTER_RESTART after watchdog has restarted a container.
while True:
    restart_status: bool = False
    container_list: list = CLIENT.containers.list()
    for container in container_list:
        container_status = container.status
        container_health_status: str = get_container_health_status(container)
        # Check if the container was restarted previously and is now healthy.
        # Send Slack/email notification. Remove from a list of restarted containers
        if container.short_id in restarted_containers and container_health_status == 'healthy':
            container_recovered(container)
            send_slack_message(notification_content)
            send_smtp_message(notification_content['text'])
        # If container is in unhealthy or exited status, restart and send Slack/Email notification.
        elif container_health_status == 'unhealthy':
            container_health_log: str = get_container_health_log(container)
            logging.error("Found container in unhealthy state! Container: '%s' has health status: '%s' and container status: '%s' with output log: %s",
                          container.name, container_health_status, container_status, container_health_log)
            restart_container(container)
            send_slack_message(notification_content)
            send_smtp_message(notification_content['text'])
            restart_status = True
        logging.debug('%s - %s - %s', container.name, container_health_status, container_status)

    # Wait to poll again, longer if restarts were done in previous loop
    if restart_status is True:
        logging.info("Waiting %s seconds until next polling, because container was restarted", polling_interval_after_restart)
        time.sleep(polling_interval_after_restart)
    elif restart_status is False:
        logging.info("All containers are in healthy state!")
        time.sleep(polling_interval)
