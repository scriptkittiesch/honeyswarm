from honeyswarm.saltapi import pepper_api
import os
import time
from datetime import datetime

from flask import Blueprint, render_template, request, abort, jsonify
from flask_security import login_required
from honeyswarm.models import Hive, PepperJobs, Frame, HoneypotEvents, Config
from honeyswarm.namegen import get_random_name

hives = Blueprint('hives', __name__, template_folder="templates")

APITOKEN = os.environ.get("HIVE_API_TOKEN")
HONEYSWARM_HOST = os.environ.get("HONEYSWARM_HOST")


@hives.route('/')
@login_required
def hives_list():

    hive_list = Hive.objects
    frame_list = Frame.objects

    config = Config.objects.first()
    honeyswarm_host = config.honeyswarm_host
    honeyswarm_api = config.honeyswarm_api

    key_list = pepper_api.salt_keys()

    for hive in hive_list:
        if str(hive.id) in key_list['minions']:
            hive.registered = True
        if str(hive.id) in key_list['minions_pre']:
            hive.registered = False

        hive.event_count = HoneypotEvents.objects(
            payload__sensor=str(hive.id)).count()

        hive.save()

    return render_template(
        "hives.html",
        honeyswarm_api=honeyswarm_api,
        honeyswarm_host=honeyswarm_host,
        hive_list=hive_list,
        key_list=key_list['minions_pre'],
        frame_list=frame_list
    )


@hives.route('/<hive_id>')
@login_required
def hive_details(hive_id):
    try:
        hive_details = Hive.objects(id=hive_id).first()
    except Exception as err:
        hive_details = err
        abort(404)

    return render_template(
        "hive_details.html",
        hive_details=hive_details
    )


@hives.route('/actions/delete', methods=["POST"])
@login_required
def hive_delete():
    """Delete hiveid from Mongo and salt master"""
    json_response = {"success": False}
    hive_id = request.form.get('hive_id')
    if not hive_id:
        json_response['message'] = "Missing Hive ID"
    else:
        try:

            # Delete Jobs
            PepperJobs.objects(hive=hive_id).delete()

            # Delete Instances
            for instance in Hive.honeypots:
                instance.delete()

            # Delete Hive
            Hive.objects(id=hive_id).delete()

            # Remove Key
            pepper_api.delete_key(hive_id)
            json_response['success'] = True
            json_response['message'] = "Hive Deleted"
        except Exception as err:
            json_response['message'] = "Error Deleting Hive: {0}".format(err)

    return jsonify(json_response)


@hives.route('/actions/poll', methods=["POST"])
@login_required
def hive_poll():
    """Poll a hive and get its grains"""
    json_response = {"success": False}
    hive_id = request.form.get('hive_id')

    if not hive_id:
        json_response['message'] = "Missing Hive ID"
    else:
        try:
            hive = Hive.objects(id=hive_id).first()
            grains_request = pepper_api.run_client_function(
                hive_id, 'grains.items')

            hive_grains = grains_request[hive_id]

            external_ip_request = pepper_api.run_client_function(
                hive_id,
                'cmd.run',
                'wget -qO- http://ipecho.net/plain'
            )
            external_ip = external_ip_request[hive_id]

            if hive_grains:
                hive_grains['external_ip'] = external_ip
                hive.grains = hive_grains
                hive.last_seen = datetime.utcnow
                hive.salt_alive = True
            else:
                hive.salt_alive = False
            hive.save()
            json_response = {"success": True}
            json_response['message'] = "Poll Complete"
        except Exception as err:
            json_response['message'] = "Error Polling Hive: {0}".format(err)

    return jsonify(json_response)


@hives.route('/actions/swarm', methods=["POST"])
@login_required
def hive_swarm():
    """Add a hive to the swarm"""
    json_response = {"success": False}
    hive_id = request.form.get('hive_id')

    if not hive_id:
        json_response['message'] = "Missing Hive ID"
    else:
        try:
            hive = Hive.objects(id=hive_id).first()
            # Accept the key
            add_key = pepper_api.accept_key(hive_id)

            # ToDo: Actually get the error
            if 'error' in add_key:
                json_response["message"] = "Error Adding Key"

            grains_request = pepper_api.run_client_function(
                hive_id, 'grains.items')
            hive_grains = grains_request[hive_id]

            # If we run too quickly grains may not be ready.
            # Try one more time.
            if not hive_grains:
                time.sleep(2)
                grains_request = pepper_api.run_client_function(
                    hive_id, 'grains.items')
                hive_grains = grains_request[hive_id]

            external_ip_request = pepper_api.run_client_function(
                hive_id,
                'cmd.run',
                'wget -qO- http://ipecho.net/plain'
            )
            external_ip = external_ip_request[hive_id]

            if hive_grains:
                hive_grains['external_ip'] = external_ip
                hive.grains = hive_grains
                hive.last_seen = datetime.utcnow
                hive.salt_alive = True
            else:
                hive.salt_alive = False

            hive.save()
            json_response = {"success": True}

            json_response['success'] = True
            json_response['message'] = "Added Hive"
        except Exception as err:
            json_response['message'] = "Error Adding to swarm: {0}".format(err)

    return jsonify(json_response)


@hives.route('/actions/frame', methods=["POST"])
@login_required
def hive_test():
    """Add Docker Frame"""
    json_response = {"success": False}
    hive_id = request.form.get('hive_id')
    frame_state_file = request.form.get('frame_state_file')
    frame_id = request.form.get('frame_id')

    if not hive_id:
        json_response['message'] = "Missing Hive ID"
    else:
        try:

            frame = Frame.objects(id=frame_id).first()
            hive = Hive.objects(id=hive_id).first()

            if hive and frame:
                job_id = pepper_api.apply_state(hive_id, [frame_state_file])

                job = PepperJobs(
                    hive=hive,
                    job_type="Apply Frame",
                    job_id=job_id,
                    job_short="Apply {0} state".format(frame.name),
                    job_description="Apply {0} frame state to Hive {1}".format(
                        frame.name, hive_id)
                )
                job.save()

                hive.frame = frame
                hive.save()

            json_response['success'] = True
            json_response['message'] = "Add Frame submitted \
                with JID: {0}".format(job.id)
        except Exception as err:
            json_response['message'] = "Error Adding to swarm: {0}".format(err)

    return jsonify(json_response)


# This is an API call to return the salt installer
@hives.route('/api/hive/register/<operating_system>')
def hives_register(operating_system):
    authenticated = False

    config = Config.objects.first()
    honeyswarm_api = config.honeyswarm_api
    honeyswarm_host = config.honeyswarm_host

    # Check for the deployment token
    if 'Authorization' in request.headers:
        auth_token = request.headers['Authorization']
        if auth_token == honeyswarm_api:
            authenticated = True
    else:
        abort(403)

    # If incorrect APITOKEN
    if not authenticated:
        abort(403)

    # Lets create a new hive
    new_hive = Hive(
        name=get_random_name()
    )

    new_hive.save()
    salt_id = str(new_hive.id)

    if operating_system == "windows":
        registration_template = "hive_registration.ps1"
    elif operating_system == "linux":
        registration_template = "hive_registration.sh"
    else:
        abort(404)

    # Return the Installation Script
    return render_template(
        registration_template,
        honeyswarm_host=honeyswarm_host,
        salt_minion_id=salt_id
    )
