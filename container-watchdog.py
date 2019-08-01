import docker
import logging
import time
import json
import requests
import os

# Set logging options and variables
logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.INFO)
timerRestarted = int(os.getenv('POLLING_INTERVAL_AFTER_RESTART', '60'))
timerOK = int(os.getenv('POLLING_INTERVAL', '10'))
dockerHost = os.getenv('DOCKER_HOSTMACHINE', 'UNKNOWN')
slack_webhook_url = os.getenv('SLACK_WEBHOOK_URL', '')
restartedContainers = []
slack_message_content = {}

# Test and establish connection to docker socket
try:
    client = docker.from_env()
    client.version()
    logging.info("Connection to Docker socket OK")
except Exception as e:
    logging.fatal("Cannot connect to Docker daemon, make sure /var/run/docker.sock is usable for watchdog!")
    logging.fatal("%s", e)
    exit()

# Function for sending Slack webhook messages
def sendSlackMessage(slack_message_content):
    if slack_webhook_url != "":
        try:
            requests.post(slack_webhook_url, data=json.dumps(slack_message_content), headers={'Content-Type': 'application/json'})
            logging.info("Message sent to Slack webhook: %s", slack_message_content['text'])
        except Exception as e:
            logging.error("%s", e)


# Run loop indefinetly polling every 30 seconds normally or in 15 minutes after watchdog has restarted a container.
while True:
    restartStatus = False
    ContainerList = client.containers.list(all)
    for container in ContainerList:
        containerStatus = container.status
# Check if Healthstatus is set for the container and If 'Health' key doesn't exist for container(healthcheck not set), log exception.
        try:
            healthStatus = container.attrs['State']['Health']['Status']
        except KeyError:
            logging.debug("Healthcheck has not been defined for container %s", container.name)
            healthStatus = 'nokey'
# Check if the container was restarted previously and is now healthy, log and send Slack message if recovered and remove from a list of restarted containers
        if container.short_id in restartedContainers and healthStatus == 'healthy':
            logging.info("Container %s has recovered and is now healthy!", container.name)
            slack_message_content['text'] = ("[Container watchdog]: Container: [ *_{0}_* ] has *recovered* with healthstatus: [ _{1}_ ] and state: [ _{2}_ ]"
            " on hostmachine [ _{3}_ ]".format(container.name, healthStatus, containerStatus, dockerHost))
            sendSlackMessage(slack_message_content)
            restartedContainers.remove(container.short_id)
#  If container is in unhealthy or exited status, restart. Send Slack message when trying to restart container. Add container to list of restarted containers.
        elif healthStatus == 'unhealthy' or containerStatus == 'exited':
            logging.error("Found container in unhealthy state! Container: %s has health status: %s and container status: %s",
                         container.name, healthStatus, containerStatus)
            try:
                container.restart()
                logging.info("Restarted container: %s",container.name)
                slack_message_content['text'] = ("[Container watchdog]: has *_restarted_* container: [ *_{0}_* ] which had healthstatus: [ _{1}_ ] and state:"
                                                " [ _{2}_ ] on hostmachine [ _{3}_ ]".format(container.name, healthStatus, containerStatus, dockerHost))
                sendSlackMessage(slack_message_content)
                if container.short_id not in restartedContainers:
                    restartedContainers.append(container.short_id)
            except Exception as e:
                logging.fatal("%s", e)
                slack_message_content['text'] = ("[Container watchdog]: Docker daemon failed to restart container *{0}* on hostmachine *{1}*"
                                                " with error message: _{2}_".format(container.name, dockerHost, e))
                sendSlackMessage(slack_message_content)
            restartStatus = True
        logging.debug('%s - %s - %s', container.name, healthStatus, containerStatus)
# Wait to poll again, longer if restarts were done in previous loop
    if restartStatus is True:
        logging.info("Waiting %s seconds until next polling, because container was restarted", timerRestarted)
        time.sleep(timerRestarted)
    elif restartStatus is False:
        logging.info("All containers are in healthy state!")
        time.sleep(timerOK)