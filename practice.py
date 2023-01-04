import functools #You can create partial functions in python by using the partial function from the functools library
import os  #provides functions for creating and removing a directory (folder), fetching its contents, changing and identifying the current directory, etc.
import re  #A regular expression (or RE) specifies a set of strings that matches it;
import sys
import traceback

import requests
import logging  #It can help you develop a better understanding of the flow of a program and discover scenarios that you might not even have thought of while developing.
import json
from jsonpath_ng import jsonpath, parse
from privacera_automation.common.common_utils import common_utils
from requests_toolbelt.utils import dump

from privacera_automation.common.privacera_logging.privacera_logger import plogger

tenant_data = dict()


def set_tenant_data(account_id, username, password, url):
    tenant_data['env_url'] = url.rstrip("/") + "/api"
    tenant_data['tenant_user_name'] = username
    tenant_data['tenant_user_password'] = password
    tenant_data['tenant_account_id'] = account_id


def get_text_btw_symbol(data):
    substring = re.findall('`{(.+?)}`', str(data))
    return substring


def get_variable_reference(prop_value: str):
    variable_reference = re.findall('\\${(.+?)}', prop_value)
    return variable_reference


def replace_var_reference(str_to_update: str, ref: str, replacement: str):
    plogger.info(f"Replacing ${ref} with {replacement} in {str_to_update}\n")
    result_str = str_to_update.replace("${" + ref + "}", replacement)
    return result_str


def update_data(data, replacable_value, replace_value):
    if isinstance(data, dict):
        replaced_data = data
        for k, v in replaced_data.items():
            if isinstance(v, (int, bool)) or (v is None):
                if "`{" + replacable_value + "}`" == v:
                    replaced_data[k] = replace_value
            else:
                if "`{" + replacable_value + "}`" in v:
                    replaced_data[k] = str(v).replace("`{" + replacable_value + "}`",
                                                      str(replace_value)).replace("\'", "\"")
    else:
        replaced_data = str(data).replace("`{" + replacable_value + "}`", str(replace_value)).replace("\'", "\"")
    return replaced_data


def update_variable_data(data, replacable_value, replace_value):
    if isinstance(data, dict):
        replaced_data = data
        for k, v in replaced_data.items():
            if isinstance(v, (int, bool)) or (v is None):
                if "${" + replacable_value + "}" == v:
                    replaced_data[k] = replace_value
            elif isinstance(v, list):
                replaced_data = data
            else:
                if "${" + replacable_value + "}" in v:
                    replaced_data[k] = str(v).replace("${" + replacable_value + "}",
                                                      str(replace_value)).replace("\'", "\"")
    elif isinstance(data, list):
        replaced_data = data
        for list_index in range(0, len(data)):
            list_item = data[list_index]
            list_item = update_variable_data(list_item, replacable_value, replace_value)
            data[list_index] = list_item
    else:
        replaced_data = str(data).replace("${" + replacable_value + "}", str(replace_value)).replace("\'", "\"")
    return replaced_data


def json_extract(obj, key):
    """Recursively fetch values from nested JSON."""
    arr = []

    def extract(obj, key):
        """Recursively search for values of key in JSON tree."""
        for keyEl in key:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        if k == keyEl:
                            if keyEl == key[-1]:
                                arr.append(v)
                                key.remove(keyEl)
                                break
                            key.remove(keyEl)
                            extract(v, key)
                    elif k == keyEl:
                        arr.append(v)
                        break
            elif isinstance(obj, list):
                if keyEl.isdigit():
                    element_num = int(keyEl)
                    key.remove(keyEl)
                    extract(obj[element_num], key)
                    break
                else:
                    for item in obj:
                        extract(item, key)
            return arr

    if isinstance(key, (tuple, list)):
        values = None
        for item in key:
            path = item.split("_")
            values = extract(obj, path)
        return values
    else:
        values = extract(obj, key.split("_"))
        return values if len(values) > 1 else values[0]


global_response_holder = {}


class Scenario:
    def __init__(self, name: str, description: str, interactions: dict):
        self.name = name
        self.description = description
        self.interactions = interactions

    def __str__(self):
        obj_str = (
            f"Test Scenario Name: {self.name}\n"
            f"Test Scenario Description: {self.description}\n\n"
        )
        return obj_str

    def get_name(self):
        return self.name

    def get_description(self):
        return self.description

    def get_interactions(self):
        return self.interactions


class TestScenarioExecutor:

    def __init__(self):
        self.interaction_execution_status = None
        self.set_blank_status()

    def set_blank_status(self):
        self.interaction_execution_status = []

    def set_interaction_execution_status(self, status):
        self.interaction_execution_status.append(status)

    def get_interaction_execution_status(self):
        return self.interaction_execution_status

    @staticmethod
    def check_response_attrs(actual_response: dict, expected_response: dict):
        attr_values_found = True
        for attr_name in expected_response.keys():
            expected_attr_value = expected_response[attr_name]

            if attr_name in actual_response:
                actual_attr_value = actual_response[attr_name]
                if expected_attr_value != actual_attr_value:
                    attr_values_found = False
                    test_scenario_executor.set_interaction_execution_status(False)
                    print(f"ERROR: Actual response contained {attr_name} with value {actual_attr_value}\n")
                    print(f"ERROR: Expected response contained {attr_name} with value {expected_attr_value}\n")
            else:
                attr_values_found = False
                test_scenario_executor.set_interaction_execution_status(False)
                print(f"ERROR: Did not find attribute {attr_name} in actual response")

        return attr_values_found

    @staticmethod
    def execute(test_scenario_to_execute: Scenario):
        test_scenario_executor.set_blank_status()
        interactions = test_scenario_to_execute.get_interactions()
        interaction_dict = dict()
        register_variables = dict()
        for interaction_index in range(0, len(interactions)):
            interaction_name = interactions[interaction_index]['testStepName']
            interaction_desc = interactions[interaction_index]['testDescription']
            interaction_request = interactions[interaction_index]['request']
            expected_response = interactions[interaction_index]['expectedResponse']

            dependency_satisfied = False
            if "dependsOn" in interactions[interaction_index]:
                dependency = interactions[interaction_index]["dependsOn"]
                if dependency in interaction_dict:
                    dependency_result = interaction_dict[dependency].get_response()
                    dependency_status = dependency_result.get_status()
                    if dependency_status == "PASSED":
                        dependency_satisfied = True
            else:
                dependency = None

            print("***************************" +
                  f"Starting test case {interaction_name}" +
                  "***************************\n")
            interaction_dict[interaction_name] = Interaction(interaction_desc,
                                                             interaction_request,
                                                             expected_response,
                                                             dependency)
            if dependency is None or dependency_satisfied:
                try:
                    plogger.info(interaction_dict[interaction_name])
                    current_request = interaction_dict[interaction_name].get_request()
                    if current_request.has_path_params():
                        path_params = current_request.get_path_params()
                        changes_found = get_variable_reference(path_params)
                        if changes_found:
                            for data in changes_found:
                                if data in register_variables:
                                    path_params = replace_var_reference(path_params, data,
                                                                        str(register_variables[data]))
                                    plogger.info(f"Path parameters = {path_params}")
                                else:
                                    raise AssertionError(
                                        f"ERROR: did not find {data} in {json.dumps(register_variables)}")
                            current_request.replace_path_params_in_uri(path_params)
                        else:
                            current_request.replace_path_params_in_uri(path_params)
                        interaction_dict[interaction_name].set_request(current_request)

                    request_payload = dict()
                    if current_request.has_payload():
                        current_payload = current_request.get_payload()

                        for payload_key, payload_value in current_payload.items():
                            plogger.info(f"current_payload[{payload_key}] = {payload_value}")
                            for variable in register_variables.keys():
                                payload_value = update_variable_data(payload_value, variable,
                                                                     str(register_variables[variable]))
                                current_payload[payload_key] = payload_value
                        plogger.info(f"Updating request payload to {json.dumps(current_payload)}")
                        request_payload = current_payload

                    operation = current_request.get_method()
                    uri = current_request.get_uri()
                    query_params = current_request.get_query_params()
                    headers = current_request.get_headers()
                    creds = current_request.get_auth_creds()

                    uri_vars_found = get_variable_reference(uri)
                    if uri_vars_found:
                        for var in uri_vars_found:
                            if var in register_variables:
                                uri = replace_var_reference(uri, var, str(register_variables[var]))
                                plogger.info(f"URI after replacement: {uri}\n")
                        current_request.set_uri(uri)
                        interaction_dict[interaction_name].set_request(current_request)

                    if uri.startswith("/"):
                        request_url = tenant_data["env_url"] + uri
                    else:
                        request_url = uri

                    print(f"request_url={request_url}")
                    plogger.info(f"request_url={request_url}")

                    print(f"Interaction Details:\n{interaction_dict[interaction_name]}")
                    try:
                        resp = requests.request(operation, request_url,
                                                headers=headers, params=query_params,
                                                json=request_payload, auth=creds)
                        key_to_hold = interaction_name.replace(" ", "")

                        if resp.status_code == 200:
                            response_value = resp.text
                            if "Accept" in headers.keys():
                                if "application/json" == headers["Accept"]:
                                    plogger.info("Setting the response in json format")
                                    response_value = resp.json()
                            global_response_holder[key_to_hold] = \
                                {"request": request_payload, "response": response_value}
                            if 'registerVariables' in interactions[interaction_index]:
                                interaction_var_dict = \
                                    interactions[interaction_index]['registerVariables']
                                for var_name, var_ref in interaction_var_dict.items():
                                    var_extract_expr = parse(f"$.{var_ref}")
                                    value_list_from_response = \
                                        var_extract_expr.find(global_response_holder[key_to_hold])
                                    assert value_list_from_response, \
                                        f"ERROR: Variable reference {var_ref} " + \
                                        f"could not be found in {global_response_holder[key_to_hold]}"
                                    for match in value_list_from_response:
                                        plogger.info(
                                            f"Adding key {var_name} with {match.value} in register_variables dict")
                                        register_variables[var_name] = match.value
                    except ConnectionError as e:
                        plogger.error(e)
                    except requests.Timeout as err:
                        plogger.error(err)
                    except requests.RequestException as other_err:
                        plogger.error(other_err)

                    try:
                        expected_status_code = interaction_dict[
                            interaction_name].get_expected_response().get_status_code()
                        response_code_success = resp.status_code == expected_status_code
                        assert response_code_success, \
                            f"Actual code {resp.status_code} does not equal expected code {expected_status_code}"
                        test_scenario_executor.set_interaction_execution_status(True)
                    except AssertionError:
                        resp_data = dump.dump_all(resp)
                        plogger.error(f"Failed response:\n {resp_data.decode('utf-8')}")
                        test_scenario_executor.set_interaction_execution_status(False)

                    response_attr_match = True
                    if interaction_dict[interaction_name].get_expected_response().has_attributes():
                        if resp.status_code not in [204, 404, 405]:
                            expected_attributes = interaction_dict[
                                interaction_name].get_expected_response().get_attributes()
                            for expected_attribute_name in expected_attributes.keys():
                                attr_value = expected_attributes[expected_attribute_name]
                                if isinstance(attr_value, str):
                                    attr_vars_found = get_variable_reference(attr_value)
                                    if attr_vars_found:
                                        for attr_var in attr_vars_found:
                                            if attr_var in register_variables:
                                                attr_value = \
                                                    replace_var_reference(attr_value, attr_var,
                                                                          register_variables[attr_var])
                                                plogger.info(
                                                    f"Replacing value of expected_attributes[{expected_attribute_name}] " +
                                                    f"with {attr_value}")
                                                expected_attributes[expected_attribute_name] = attr_value
                                                print(expected_attributes)
                                            else:
                                                raise AssertionError(f"ERROR: found unexpected variable {attr_var}" +
                                                                     "in expected response attributes")
                            response_attr_match = TestScenarioExecutor.check_response_attrs(resp.json(),
                                                                                            expected_attributes)
                        else:
                            raise AssertionError(
                                f"ERROR: Unable to check response attributes " +
                                f"for HTTP response status code {resp.status_code}")

                    interaction_result = interaction_dict[interaction_name].get_response()
                    interaction_result.set_status_code(resp.status_code)
                    if resp.status_code != 204:
                        if resp.status_code != 401 and headers["Accept"] == "application/json":
                            interaction_result.set_response(resp.json())
                        else:
                            interaction_result.set_response(resp.text)

                    interaction_result.set_is_passed(response_code_success and response_attr_match)
                    interaction_result.set_status(
                        "PASSED" if response_code_success and response_attr_match else "FAILED")
                except AssertionError:
                    _, _, tb = sys.exc_info()
                    traceback.print_tb(tb)
                    tb_info = traceback.extract_tb(tb)
                    filename, line, func, text = tb_info[-1]
                    plogger.error(f"An error occurred in {filename} on line {line} in statement {text}")
                    interaction_result = interaction_dict[interaction_name].get_response()
                    interaction_result.set_status("FAILED")
                    interaction_result.set_status_code(resp.status_code)
                    interaction_result.set_response(resp.text)
            else:
                plogger.error(f"ERROR: Test case {interaction_name} was " +
                              f"skipped due to dependency on test case {dependency}")
                interaction_result.set_is_passed(False)
                interaction_result.set_status("SKIPPED")
                interaction_result.set_status_code(0)
                interaction_result.set_response("")

            interaction_dict[interaction_name].set_response(interaction_result)

            print(f"Interaction result:\n{interaction_result}")
            if interaction_index == len(interactions) - 1:
                print("\nStatus for all interactions of the testcase: ",
                      test_scenario_executor.get_interaction_execution_status())
            print("\n***************************" +
                  f"Ending test case {interaction_name}" +
                  "***************************\n")


test_scenario_executor = TestScenarioExecutor()


class TestScenarioProperties:

    def __init__(self):
        self.test_scenario_dict = None
        self.test_scenarios = None
        self.test_scenario_description = None
        self.test_scenario_json = None

    def set_scenarios(self, test_scenario_json: dict):
        self.test_scenarios = test_scenario_json
        self.test_scenario_dict = dict()
        plogger.info(f"Test scenario id = {self.test_scenarios['testCaseName']}")
        test_scenario_name = self.test_scenarios['testCaseName']
        test_scenario_description = \
            self.test_scenarios['description']
        test_scenario_interactions = \
            self.test_scenarios['interactions']
        test_scenario = Scenario(test_scenario_name, test_scenario_description, test_scenario_interactions)
        self.test_scenario_dict = test_scenario
        plogger.info(f"Scenario = {self.test_scenario_dict}")

    def get_scenarios(self):
        return self.test_scenario_dict


test_scenarios_properties = TestScenarioProperties()


class Logger:
    """
    A Ranger API automation logger which will take care
    of logging to file.
    """

    def __init__(self, name, log_file, level):
        """
        Constructor
        """
        self._formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.name = name
        self.log_file = log_file
        self.level = level

    def setup_logger(self):
        """To setup multiple loggers """

        handler = logging.FileHandler(self.log_file)
        handler.setFormatter(self._formatter)

        logger = logging.getLogger(self.name)
        logger.setLevel(self.level)
        logger.addHandler(handler)

        return logger


# str_date_time = datetime.now().strftime("%Y%m%d%H%M%S")
# plogger = Logger('application_logger', f'Logs/info_{str_date_time}.log', level=logging.INFO)
# plogger = Logger('application_logger', f'Logs/error_{str_date_time}.log', level=logging.DEBUG)

# plogger = plogger.setup_logger()
# plogger = plogger.setup_logger()


class InteractionResult:
    def __init__(self, status_code, response, is_passed: bool):
        self.status_code = status_code
        self.response = response
        self.is_passed = is_passed
        self.status = "FAILED"

    def __str__(self):
        result_str = "PASSED" if self.is_passed else "FAILED"
        obj_str = (
            f"Interaction code: {self.status_code}\n"
            f"Interaction test result: {self.status}\n"
        )
        if self.response:
            obj_str += f"Interaction response: {json.dumps(self.response)}\n"
        obj_str += "\n"
        return obj_str

    def get_status_code(self):
        return self.status_code

    def set_status_code(self, status_code):
        self.status_code = status_code

    def get_response(self):
        return self.response

    def set_response(self, response):
        self.response = response

    def get_is_passed(self):
        return self.is_passed

    def set_is_passed(self, is_passed: bool):
        self.is_passed = is_passed

    def get_status(self):
        return self.status

    def set_status(self, status: str):
        self.status = status


class InteractionRequest:
    def __init__(self, request_details: dict):
        self.path_params = None
        self.payload = None
        self.query_params = None

        for key in request_details.keys():
            if key == "endpointURI":
                self.uri = request_details[key]
            elif key == "method":
                self.method = request_details[key]
            elif key == "headers":
                self.headers = request_details[key]
            elif key == "queryParams":
                self.query_params = request_details[key]
            elif key == "pathParams":
                self.path_params = request_details[key]
            elif key == "payload":
                if isinstance(request_details[key], dict):
                    self.payload = request_details[key]
                elif isinstance(request_details[key], str):
                    payload_json_path = request_details[key]
                    try:
                        with open(payload_json_path, encoding='utf-8') as payload_file:
                            self.payload = json.load(payload_file)
                    except IOError as ioe:
                        plogger.error(f"ERROR: File {payload_json_path} could not be opened!")
                        plogger.error(ioe)
                    finally:
                        payload_file.close()
                else:
                    assert False, f"ERROR: Unknown payload type {type(request_details[key])}"
            elif key == "authentication":
                self.auth_creds = tuple(request_details[key].values())
            else:
                print(f"Error: unknown key {key} submitted!")

        if "headers" not in request_details:
            self.headers = self.get_default_headers()

        if "authentication" not in request_details:
            self.auth_creds = tenant_data["tenant_user_name"], tenant_data["tenant_user_password"]

        if "queryParams" not in request_details:
            self.query_params = self.get_default_query_params()

    def __str__(self):
        request_str = (
            f"Request URI: {self.uri}\n"
            f"Request Type: {self.method}\n"
            f"Request Headers: {self.headers}\n"
            f"Query Params: {self.query_params}\n"
            f"Authorization credentials: {self.auth_creds}\n"
        )
        if self.payload is not None:
            request_str += f"Request Body: {self.payload}\n"

        if self.has_path_params():
            request_str += f"Path parameters = {self.path_params}\n"

        request_str += "\n"
        return request_str

    @staticmethod
    def get_default_headers():
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        return headers

    @staticmethod
    def get_default_query_params():
        query_params = {'accountId': tenant_data["tenant_account_id"]}
        return query_params

    def get_headers(self):
        return self.headers

    def get_uri(self):
        return self.uri

    def set_uri(self, uri):
        self.uri = uri

    def get_method(self):
        return self.method

    def get_query_params(self):
        return self.query_params

    def get_path_params(self):
        return self.path_params

    def has_path_params_dependency(self):
        return "dependsOn" in self.path_params

    def get_payload(self):
        return self.payload

    def get_payload_body(self):
        return self.payload

    def has_payload_dependency(self):
        return "dependsOn" in self.payload

    def has_payload_update(self):
        return "update" in self.payload

    def has_payload(self):
        return self.payload is not None

    def get_auth_creds(self):
        return self.auth_creds

    def has_path_params(self):
        return self.path_params is not None

    def set_path_params(self, path_params):
        self.path_params = path_params

    def replace_path_params_in_uri(self, replacement_value):
        print(f"Appending {replacement_value} to {self.uri}\n")
        self.set_uri(self.uri + replacement_value)


class InteractionExpectedResponse:
    def __init__(self, response_details: dict):
        self.attributes = None
        self.headers = None
        self.status_code = response_details["statusCode"]
        if "attributes" in response_details:
            self.attributes = response_details["attributes"]
        if "headers" in response_details:
            self.headers = response_details["headers"]

    def __str__(self):
        obj_str = (
            f"Expected status code = {self.status_code}\n"
        )
        if self.attributes is not None:
            obj_str += f"Expected attributes in response = {self.attributes}"
        if self.headers is not None:
            obj_str += f"Expected headers in response = {self.headers}"
        obj_str += "\n"
        return obj_str

    def get_status_code(self):
        return self.status_code

    def get_attributes(self):
        return self.attributes

    def has_attributes(self):
        return self.attributes is not None


class Interaction:
    interaction_id: str

    def __init__(self, description: str, request: dict, expected_response: dict, dependency=None):
        self.description = description
        self.request = InteractionRequest(request)
        self.expected_response = InteractionExpectedResponse(expected_response)
        self.response = InteractionResult(None, None, False)
        self.dependency = dependency

    def __str__(self):
        obj_str = (
            f"Interaction Description: {self.description}\n"
            f"Interaction Request Details:\n{self.request}\n"
            f"Interaction Expected Response:\n{self.expected_response}\n"
        )
        if self.has_dependency():
            obj_str += f"Depends on Upstream Test Case: {self.dependency}\n"
        return obj_str

    def get_interaction_id(self):
        return self.interaction_id

    def get_description(self):
        return self.description

    def get_request(self):
        return self.request

    def get_expected_response(self):
        return self.expected_response

    def set_request(self, request: InteractionRequest):
        self.request = request

    def get_response(self):
        return self.response

    def set_response(self, response: InteractionResult):
        self.response = response

    def has_dependency(self):
        return self.dependency is not None

    def get_dependency(self):
        return self.dependency


@functools.cache
def load_bulk_testcases():
    test_case_accumulated = []
    try:
        for path, subdirs, files in os.walk(common_utils.get("privacera.portal.testcases.dir")):
            for name in files:
                if ".json" in name:
                    data = json.loads(open(os.path.join(path, name)).read())
                    test_case_accumulated.extend(data)
    except Exception as ex:
        plogger.exception("run_bulk_testcases - got exception")
        raise
    return test_case_accumulated
