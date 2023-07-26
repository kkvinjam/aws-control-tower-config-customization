# #
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify,merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#

import boto3
import cfnresource
import os
import logging
import ast

CFT_MESSAGE = 'overriding config recorder for ALL accounts because of first run after function deployment from CloudFormation'

def process_eb_event(event, sqs_url, accounts_list, action):
    '''
    Function to process EventBridge events.
    '''

    event_source = event['source']
    event_detail = event['detail']
    service_event = event_detail['serviceEventDetails']
    logging.info(f'Event Source: {event_source}')
    logging.info(f'Service Event: {service_event}')

    if event_source == 'aws.controltower':
        event_name = event_detail['eventName']
        logging.info(f'EventBridge Event Name: {event_name}')
        logging.info(f'Event Source: {event_source}')
        logging.info(f'Event Name: {event_name}')

        if event_name == 'UpdateManagedAccount':
            account = service_event['updateManagedAccountStatus']['account']['accountId']
            logging.info(f'overriding config recorder for SINGLE account: {account}')
            override_config_recorder(accounts_list, sqs_url, account, 'controltower', action)
        elif event_name == 'CreateManagedAccount':
            account = service_event['createManagedAccountStatus']['account']['accountId']
            logging.info(f'overriding config recorder for SINGLE account: {account}')
            override_config_recorder(accounts_list, sqs_url, account, 'controltower', action)
        elif event_name == 'UpdateLandingZone':
            logging.info('overriding config recorder for ALL accounts due to UpdateLandingZone event')
            override_config_recorder(accounts_list, sqs_url, '', 'controltower', action)
        else:
            logging.info('No action taken')
    else:
        logging.info('Unsupported event recieved: {event_source}')


def process_cft_event(event, context, sqs_url, acounts_list, action):
    '''
    Function to process CloudFormation events.
    '''

    request_type = event['RequestType']
    logging.info(f'Request Type: {request_type}')
    response = {}

    if request_type == 'Create':
        logging.info('CREATE : {CFT_MESSAGE}')
        override_config_recorder(acounts_list, sqs_url, '', 'Create', action)
        cfnresource.send(event, context, cfnresource.SUCCESS, response, "CustomResourcePhysicalID")
    elif request_type == 'Update':
        logging.info('UPDATE : {CFT_MESSAGE}')
        override_config_recorder(acounts_list, sqs_url, '', 'Update', action)
        update_account_list(acounts_list, sqs_url)
        cfnresource.send(event, context, cfnresource.SUCCESS, response, "CustomResourcePhysicalID")
    elif request_type == 'Delete':
        logging.info('DELETE : {CFT_MESSAGE}')
        override_config_recorder(acounts_list, sqs_url, '', 'Delete', action)
        cfnresource.send(event, context, cfnresource.SUCCESS, response, "CustomResourcePhysicalID")
    else:
        logging.info('Unsupported Request Type: {request_type}')
        cfnresource.send(event, context, cfnresource.SUCCESS, response, "CustomResourcePhysicalID")


def lambda_handler(event, context):
    '''
    Lambda handler function.
    '''

    LOG_LEVEL = os.getenv('LOG_LEVEL')
    logging.getLogger().setLevel(LOG_LEVEL)

    try:
        logging.info('Event Data: ')
        logging.info(event)
        sqs_url = os.getenv('SQS_URL')
        acounts_list = os.getenv('ACCOUNTS_LIST')
        action = os.getenv('ACTION')
        logging.info(f'Excluded Accounts: {acounts_list}')
        sqs_client = boto3.client('sqs')

        # Check if the lambda was trigerred from EventBridge.
        # If so extract Account and Event info from the event data.

        if 'source' in event:
            event_source = event['source']
            logging.info(f'EventBridge event recieved: {event_source}')
            process_eb_event(event, sqs_url, acounts_list, action)
        elif 'LogicalResourceId' in event:
            logging.info('CloudFormation event recieved')
            process_cft_event(event, context, sqs_url, acounts_list, action)
        else:
            logging.info("No matching event found")

        logging.info('Execution Successful')

        # TODO implement
        return {
            'statusCode': 200
        }

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')


def override_config_recorder(acounts_list, sqs_url, account, event, action):
    '''
    Function to override config recorder for all accounts.
    '''

    CONFIG_STACK='AWSControlTowerBP-BASELINE-CONFIG'
    try:
        client = boto3.client('cloudformation')
        paginator = client.get_paginator('list_stack_instances')
        instance_list = []
        sqs_client = boto3.client('sqs')

        if not account:
            page_iterator = paginator.paginate(StackSetName=CONFIG_STACK)
        else:
            page_iterator = paginator.paginate(StackSetName=CONFIG_STACK, StackInstanceAccount=account)

        for page in page_iterator:
            instance_list.extend(page['Summaries'])

        for item in instance_list:
            account = item['Account']
            region = item['Region']
            send_message_to_sqs(event, account, region, acounts_list, sqs_client, sqs_url, action)

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')


def send_message_to_sqs(event, account, region, acounts_list, sqs_client, sqs_url, action):
    '''
    Function to send message to SQS.
    '''
    try:

        #Proceed only if the account is not excluded
        if action == 'EXLUDEACCOUNTS':
            if account not in acounts_list:
                #construct sqs message
                sqs_msg = f'{{"Account": "{account}", "Region": "{region}", "Event": "{event}"}}'
                #send message to sqs
                response = sqs_client.send_message(
                QueueUrl=sqs_url,
                MessageBody=sqs_msg)
                logging.info(f'message sent to sqs: {sqs_msg}')
            else:
                logging.info(f'Account excluded: {account}')
        elif action == 'INCLUDEACCOUTNS':
            if account in acounts_list:
                #construct sqs message
                sqs_msg = f'{{"Account": "{account}", "Region": "{region}", "Event": "{event}"}}'
                #send message to sqs
                response = sqs_client.send_message(
                QueueUrl=sqs_url,
                MessageBody=sqs_msg)
                logging.info(f'message sent to sqs: {sqs_msg}')
            else:
                logging.info(f'Account excluded: {account}')
        else:
            logging.info(f'Unsupported action: {action}, No action taken')

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')


def update_account_list(account_list,sqs_url):

    try:
        sts_client = boto3.client('sts')
        new_account_list = "['" + sts_client.get_caller_identity().get('Account') + "']"
        logging.info(f'templist: {new_account_list}')
        templist=ast.literal_eval(account_list)

        templist_out=[]

        for acct in templist:
            if sts_client.get_caller_identity().get('Account') != acct:
                templist_out.append(acct)
                logging.info(f'Delete request sent: {acct}')
                override_config_recorder(new_account_list, sqs_url, acct, 'Delete')

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')
