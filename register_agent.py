#!/usr/bin/env python3

import json
import os
import sys
from subprocess import PIPE, Popen  # nosec

import psutil
import urllib3
from base64 import b64encode
from healthcheck import HealthCheck
from jinja2 import Template
from loguru import logger
from http.server import BaseHTTPRequestHandler, HTTPServer
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
try:
    import requests
except ModuleNotFoundError as e:
    logger.error("No module 'requests' found. Install: pip install requests")
    sys.exit(1)

health = HealthCheck()

# Global variables for caching the token
cached_token = None
token_expiration = 0

def get_auth_token():
    global cached_token, token_expiration
    
    # Check if the token is still valid
    if cached_token and time.time() < token_expiration:
        logger.info(f"Token is not expired, will use the cached token")
        return cached_token
    
    logger.info(f"Token is empty or got expired, renewing a new token")
    # Token is expired or not available, fetch a new one
    login_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {b64encode(auth).decode()}",
    }
    response = requests.get(login_url, headers=login_headers, verify=False)  # nosec
    if response.status_code == 200:
        token_data = response.json()
        cached_token = token_data["data"]["token"]
        # Set token expiration time (e.g., 5 minutes)
        token_expiration = time.time() + 300
        return cached_token
    else:
        logger.error(f"Failed to get authentication token: {response.content}")
        raise Exception("Failed to get authentication token")


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        message, status_code, headers = health.run()
        try:
            request_path = str(self.path).replace("\n", " ")
            response_msg = http_codes_serializer(
                response=json.loads(message), status_code=status_code
            )
            logger.info(
                f"GET request. path: {request_path} headers: {headers}, response: {response_msg}"
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(bytes(message, encoding="utf8"))
        except TypeError:
            self.send_response(500)


def json_serialize(record):
    subset = {"timestamp": record["time"].timestamp(), "message": record["message"]}
    return json.dumps(subset)


def get_serialize(message):
    serialized = json_serialize(message.record)
    print(serialized)


def http_codes_serializer(response, status_code):
    msg = json.dumps(response, indent=4, sort_keys=True)
    code = f"status: {status_code} - {code_desc(status_code)}"
    return f"{json.loads(msg)} {code}"


def create_config_file():
    logger.info(f"Create Wazuh agent configuration for node {node_name}")
    with open("ossec.jinja2") as file_:
        template = Template(file_.read(), autoescape=True)
        config = template.render(
            join_manager_hostname=join_manager_worker,
            join_manager_port=join_manager_port,
            virus_total_key=virus_total_key,
        )
    wazuh_config_file = open("/var/ossec/etc/ossec.conf", "w")
    wazuh_config_file.write(f"{config} \n")
    wazuh_config_file.close()
    open("/var/ossec/etc/local_internal_options.conf", "wb").write(
        open("local_internal_options.jinja2", "rb").read()
    )
    logger.info(
        "Configuration has been generated from template, starting Wazuh agent provisioning"
    )


def wazuh_api(method, resource, data=None):
    global cached_token

    try:
        # Get the current token
        token = get_auth_token()
        
        requests_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        url = f"{base_url}/{resource}"
        
        if method.lower() == "post":
            response = requests.post(
                url, headers=requests_headers, data=json.dumps(data), verify=verify
            )
        elif method.lower() == "put":
            response = requests.put(
                url, headers=requests_headers, data=data, verify=verify
            )
        elif method.lower() == "delete":
            response = requests.delete(
                url, headers=requests_headers, data=data, verify=verify
            )
        else:
            response = requests.get(
                url, headers=requests_headers, params=data, verify=verify
            )

        code = response.status_code
        response_json = response.json()

    except Exception as exception:
        logger.error(f"Error: for resource {resource}, exception {exception}")
        code = None
        response_json = {}

    return code, response_json

def check_self():
    process_name = os.path.basename(__file__)
    for proc in psutil.process_iter():
        for process in process_name:
            if process in proc.name():
                return True, "register_agent ok"


health.add_check(check_self)


def code_desc(http_status_code):
    return requests.status_codes._codes[http_status_code][0]


def add_agent_to_group(wazuh_agent_id, agent_group):
    # Retrieve the current list of groups for the agent
    status_code, response = wazuh_api("get", f"agents?agents_list={wazuh_agent_id}")
    response_msg = http_codes_serializer(response=response, status_code=status_code)

    if status_code == 200 and response["error"] == 0:
        # Extract the current groups from the response
        current_groups = response["data"]["affected_items"][0].get("group", [])
        
        # Check if the agent is already in the specified group
        if agent_group in current_groups:
            logger.info(f"Wazuh agent ID {wazuh_agent_id} is already in group {agent_group}. Skipping addition.")
            return

    # Proceed to add the agent to the group if not already present
    wait_time = os.environ.get("WAZUH_WAIT_TIME", default="30")
    status_code, response = wazuh_api(
        "put",
        f"agents/{wazuh_agent_id}/group/{agent_group}?pretty=true&wait_for_complete=true",
    )
    response_msg = http_codes_serializer(response=response, status_code=status_code)

    if status_code == 200 and response["error"] == 0:
        logger.info(
            f"Wazuh agent id {wazuh_agent_id} has been assigned to group {agent_group}. Response {response_msg}"
        )
        return response
    else:
        logger.error(f"ERROR: Unable to add agent to group {response_msg}, retry")
        logger.info(f"Will try to add agent to group again in {wait_time}, sleeping ......")
        time.sleep(int(wait_time))
        add_agent_to_group(wazuh_agent_id, agent_group)


def add_agent(agt_name, agt_ip=None):
    if agt_ip:
        status_code, response = wazuh_api(
            "post",
            "agents/insert",
            {
                "name": agt_name,
                "ip": agt_ip,
                "force": {
                    "enabled": True,
                    "disconnected_time": {"enabled": False, "value": "0"},
                    "after_registration_time": "1s",
                },
            },
        )
    else:
        status_code, response = wazuh_api(
            "post",
            "agents/insert",
            {
                "name": str(agt_name),
                "force": {
                    "enabled": True,
                    "disconnected_time": {"enabled": False, "value": "0"},
                    "after_registration_time": "1s",
                },
            },
        )
    response_msg = http_codes_serializer(response=response, status_code=status_code)
    if status_code == 400:
        logger.error(f"During adding Wazuh agent request return {response_msg}")
        pass
    elif status_code == 200 and response["error"] == 0:
        wazuh_agent_id = response["data"]["id"]
        wazuh_agent_key = response["data"]["key"]
        logger.info(
            f"Wazuh agent for node '{node_name}' with ID '{wazuh_agent_id}' has been added. Response {response_msg}"
        )
        return wazuh_agent_id, wazuh_agent_key
    else:
        logger.error(f"Unable to add agent {agt_name}: {response_msg}")


def wazuh_agent_status(agt_name, pretty=None):
    wazuh_agnt_name = None
    wazuh_agnt_status = None
    wazuh_agnt_id = None
    if pretty:
        status_code, response = wazuh_api(
            "get", f"   "
        )
    else:
        status_code, response = wazuh_api(
            "get", f"agents?q=name={agt_name}&wait_for_complete=true"
        )
    response_msg = http_codes_serializer(response=response, status_code=status_code)
    if status_code == 200 and response["error"] == 0:
        for items in response["data"]["affected_items"]:
            wazuh_agnt_name = items["name"]
            wazuh_agnt_status = items["status"]
            wazuh_agnt_id = items["id"]
        logger.info(f"Wazuh agent status: {response_msg}")
        return wazuh_agnt_name, wazuh_agnt_status, wazuh_agnt_id
    else:
        logger.error(f"Unable to get Wazuh agent status: {response_msg}")
        return None, None, None


def wazuh_agent_import_key(wazuh_agent_key):
    cmd = "/var/ossec/bin/manage_agents"
    std_out, std_err, return_code = execute([cmd, "-i", wazuh_agent_key], "y\n\n")
    if return_code != 0:
        msg = std_err.replace("\n", " ")
        logger.error(f"Error during importing key: {msg}")
    else:
        msg = std_out.replace("\n", " ")
        logger.info(f"Key has been imported {msg}")


def execute(cmd_list, stdin=None):
    process = Popen(
        cmd_list,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        encoding="utf8",
        shell=False,  # nosec
    )
    process_out, process_err = process.communicate(stdin)
    return_code = process.returncode
    return process_out, process_err, return_code


def restart_wazuh_agent():
    cmd = "/var/ossec/bin/wazuh-control"
    command_stdout, command_stderr, return_code = execute([cmd, "restart"])
    restarted = False

    for line_output in command_stdout.split(os.linesep):
        if "Completed." in line_output:
            restarted = True
            logger.info("Wazuh agent has been restarted")
            break

    if not restarted:
        logger.error(f"error during restarting Wazuh agent: {command_stderr}")

def get_agent_key(agent_id):
    # Function to retrieve agent key
    status_code, response = wazuh_api("get", f"agents/{agent_id}/key")
    response_msg = http_codes_serializer(response=response, status_code=status_code)
    agent_key = None
    if status_code == 200 and response["error"] == 0:
        for items in response["data"]["affected_items"]:
            agent_key = items["key"]
            logger.info(f"Agent key retrieved successfully for agent ID '{agent_id}'")
            return agent_key
        logger.error(f"Unable to retrieve agent key for agent ID '{agent_id}': {response_msg}")    
    else:
        logger.error(f"Failed to retrieve agent key for agent ID '{agent_id}': {response_msg}")
        return None

if __name__ == "__main__":
    logger.remove()
    logger.add(get_serialize)
    protocol = os.environ.get("JOIN_MANAGER_PROTOCOL", default="https")
    host = os.environ.get(
        "JOIN_MANAGER_MASTER_HOST", default="wazuh.wazuh.svc.cluster.local"
    )
    user = os.environ.get("JOIN_MANAGER_USER", default="")
    password = os.environ.get("JOIN_MANAGER_PASSWORD", default="")
    node_name = os.environ.get("NODE_NAME")
    port = os.environ.get("JOIN_MANAGER_API_PORT")
    join_manager_port = os.environ.get("JOIN_MANAGER_PORT", default=1514)
    groups = os.environ.get("WAZUH_GROUPS", default="default")
    virus_total_key = os.environ.get("VIRUS_TOTAL_KEY")
    join_manager_worker = os.environ.get(
        "JOIN_MANAGER_WORKER_HOST", default="wazuh-workers.wazuh.svc.cluster.local"
    )
    wait_time = os.environ.get("WAZUH_WAIT_TIME", default="10")
    flask_bind = os.environ.get("FLASK_BIND", default="0.0.0.0")
    if not node_name:
        node_name = os.environ.get("HOSTNAME")
    login_endpoint = "security/user/authenticate"
    base_url = f"{protocol}://{host}:{port}"
    login_url = f"{protocol}://{host}:{port}/{login_endpoint}"
    auth = f"{user}:{password}".encode()
    verify = False
    create_config_file()
    # Check if the agent exists before adding it
    agent_name, agent_status, agent_id = wazuh_agent_status(node_name)
    agent_key = None
    if agent_id is None:
        agent_id, agent_key = add_agent(node_name)
    else:
        logger.info(f"Wazuh agent '{agent_name}' already exists. Status: {agent_status}")
        # If the agent already exists, retrieve the agent key
        agent_key = get_agent_key(agent_id)
    if agent_key is None:
        raise ValueError("Failed to retrieve agent key.")
    wazuh_agent_import_key(agent_key.encode())
    restart_wazuh_agent()
    status = True
    while status:
        agent_name, agent_status, agent_id = wazuh_agent_status(node_name)
        if agent_status == "active":
            logger.info(
                f"Wazuh agent '{agent_name}' is ready and connected,  status - '{agent_status}......"
            )
            logger.info(
                f"Wazuh Agent {agent_name} has been connected to server {join_manager_worker}......"
            )
            status = False
        else:
            logger.info(
                f"Waiting for Wazuh agent {agent_name} become ready current status is {agent_status}......"
            )
            logger.info(f"Will check the agent status again in {wait_time}, sleeping....")
            time.sleep(int(wait_time))
    if groups == "default":
        pass
    else:
        for group in list(groups.split(",")):
            add_agent_to_group(agent_id, group)
    logger.info("Listening on 0.0.0.0:5000")
    server = HTTPServer(("0.0.0.0", 5000), RequestHandler)
    server.serve_forever()
