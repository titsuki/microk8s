#!/usr/bin/python3
import base64
import subprocess
import os
import getopt
import sys
import time

import requests
import socket
import shutil
import urllib3

from common.utils import try_set_file_permissions, get_callback_token
import yaml

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
CLUSTER_API = "cluster/api/v1.0"
snapdata_path = os.environ.get('SNAP_DATA')
snap_path = os.environ.get('SNAP')
ca_cert_file_via_env = "${SNAP_DATA}/certs/ca.remote.crt"
ca_cert_file = "{}/certs/ca.crt".format(snapdata_path)
ca_cert_key_file = "{}/certs/ca.key".format(snapdata_path)
server_cert_file = "{}/certs/server.crt".format(snapdata_path)
server_cert_key_file = "{}/certs/server.key".format(snapdata_path)
service_account_key_file = "{}/certs/serviceaccount.key".format(snapdata_path)
cluster_dir = "{}/var/kubernetes/backend".format(snapdata_path)
cluster_backup_dir = "{}/var/kubernetes/backend.backup".format(snapdata_path)
cluster_cert_file = "{}/cluster.crt".format(cluster_dir)
cluster_key_file = "{}/cluster.key".format(cluster_dir)
callback_token_file = "{}/credentials/callback-token.txt".format(snapdata_path)
callback_tokens_file = "{}/credentials/callback-tokens.txt".format(snapdata_path)


def get_connection_info(master_ip, master_port, token):
    """
    Contact the master and get all connection information

    :param master_ip: the master IP
    :param master_port: the master port
    :param token: the token to contact the master with
    :return: the json response of the master
    """
    cluster_agent_port = 25000
    filename = "{}/args/cluster-agent".format(snapdata_path)
    with open(filename) as fp:
        for _, line in enumerate(fp):
            if line.startswith("--port"):
                cluster_agent_port = line.split(' ')
                cluster_agent_port = cluster_agent_port[-1].split('=')
                cluster_agent_port = cluster_agent_port[0].rstrip()

    req_data = {"token": token,
                "hostname": socket.gethostname(),
                "port": cluster_agent_port}

    # TODO: enable ssl verification
    connection_info = requests.post("https://{}:{}/{}/join".format(master_ip, master_port, CLUSTER_API),
                                    json=req_data,
                                    verify=False)
    if connection_info.status_code != 200:
        print("Failed to join cluster. {}".format(connection_info.json()["error"]))
        exit(1)
    return connection_info.json()


def usage():
    print("Join a cluster: microk8s.join <master>:<port>/<token>")


def set_arg(key, value, file):
    """
    Set an arguement to a file

    :param key: argument name
    :param value: value
    :param file: the arguments file
    """
    filename = "{}/args/{}".format(snapdata_path, file)
    filename_remote = "{}/args/{}.remote".format(snapdata_path, file)
    done = False
    with open(filename_remote, 'w+') as back_fp:
        with open(filename, 'r+') as fp:
            for _, line in enumerate(fp):
                if line.startswith(key):
                    done = True
                    if value is not None:
                        back_fp.write("{} {}\n".format(key, value))
                else:
                    back_fp.write("{}".format(line))
        if not done and value is not None:
            back_fp.write("{} {}\n".format(key, value))

    shutil.copyfile(filename, "{}.backup".format(filename))
    try_set_file_permissions("{}.backup".format(filename))
    shutil.copyfile(filename_remote, filename)
    try_set_file_permissions(filename)
    os.remove(filename_remote)


def ca_one_line(ca):
    """
    The CA in one line
    :param ca: the ca
    :return: one line
    """
    return base64.b64encode(ca.encode('utf-8')).decode('utf-8')


def create_kubeconfig(token, ca, master_ip, api_port, filename, user):
    """
    Create a kubeconfig file. The file in stored under credentials named after the user

    :param token: the token to be in the kubeconfig
    :param ca: the ca
    :param master_ip: the master node IP
    :param api_port: the API server port
    :param filename: the name of the config file
    :param user: the user to use al login
    """
    snap_path = os.environ.get('SNAP')
    config_template = "{}/microk8s-resources/{}".format(snap_path, "kubelet.config.template")
    config = "{}/credentials/{}".format(snapdata_path, filename)
    shutil.copyfile(config, "{}.backup".format(config))
    try_set_file_permissions("{}.backup".format(config))
    ca_line = ca_one_line(ca)
    with open(config_template, 'r') as tfp:
        with open(config, 'w+') as fp:
            config_txt = tfp.read()
            config_txt = config_txt.replace("CADATA", ca_line)
            config_txt = config_txt.replace("NAME", user)
            config_txt = config_txt.replace("TOKEN", token)
            config_txt = config_txt.replace("127.0.0.1", master_ip)
            config_txt = config_txt.replace("16443", api_port)
            fp.write(config_txt)
        try_set_file_permissions(config)


def create_admin_kubeconfig(ca):
    """
    Create a kubeconfig file. The file in stored under credentials named after the admin

    :param ca: the ca
    """
    snap_path = os.environ.get('SNAP')
    token = get_token("admin", "basic_auth.csv")
    config_template = "{}/microk8s-resources/{}".format(snap_path, "client.config.template")
    config = "{}/credentials/client.config".format(snapdata_path)
    shutil.copyfile(config, "{}.backup".format(config))
    try_set_file_permissions("{}.backup".format(config))
    ca_line = ca_one_line(ca)
    with open(config_template, 'r') as tfp:
        with open(config, 'w+') as fp:
            config_txt = tfp.read()
            config_txt = config_txt.replace("CADATA", ca_line)
            config_txt = config_txt.replace("NAME", "admin")
            config_txt = config_txt.replace("AUTHTYPE", "password")
            config_txt = config_txt.replace("PASSWORD", token)
            fp.write(config_txt)
        try_set_file_permissions(config)


def update_kubeproxy(token, ca, master_ip, api_port, hostname_override):
    """
    Configure the kube-proxy

    :param token: the token to be in the kubeconfig
    :param ca: the ca
    :param master_ip: the master node IP
    :param api_port: the API server port
    :param hostname_override: the hostname override in case the hostname is not resolvable
    """
    create_kubeconfig(token, ca, master_ip, api_port, "proxy.config", "kubeproxy")
    set_arg("--master", None, "kube-proxy")
    if hostname_override:
        set_arg("--hostname-override", hostname_override, "kube-proxy")

    subprocess.check_call("systemctl restart snap.microk8s.daemon-proxy.service".split())


def update_kubelet(token, ca, master_ip, api_port):
    """
    Configure the kubelet

    :param token: the token to be in the kubeconfig
    :param ca: the ca
    :param master_ip: the master node IP
    :param api_port: the API server port
    """
    create_kubeconfig(token, ca, master_ip, api_port, "kubelet.config", "kubelet")
    set_arg("--client-ca-file", "${SNAP_DATA}/certs/ca.remote.crt", "kubelet")
    subprocess.check_call("systemctl restart snap.microk8s.daemon-kubelet.service".split())


def store_remote_server_cert(cert, cert_key):
    with open(server_cert_file, 'w+') as fp:
        fp.write(cert)
    try_set_file_permissions(server_cert_file)
    with open(server_cert_key_file, 'w+') as fp:
        fp.write(cert_key)
    try_set_file_permissions(server_cert_key_file)


def store_remote_service_account_key(key):
    with open(service_account_key_file, 'w+') as fp:
        fp.write(key)
    try_set_file_permissions(service_account_key_file)


def store_remote_ca(ca, ca_key):
    """
    Store the remote ca

    :param ca: the CA
    """
    with open(ca_cert_file, 'w+') as fp:
        fp.write(ca)
    try_set_file_permissions(ca_cert_file)
    with open(ca_cert_key_file, 'w+') as fp:
        fp.write(ca_key)
    try_set_file_permissions(ca_cert_key_file)


def store_cluster_certs(cluster_cert, cluster_key):
    """
    Store the cluster certs

    :param cluster_cert: the cluster certificate
    :param cluster_key: the cluster certificate key
    """
    with open(cluster_cert_file, 'w+') as fp:
        fp.write(cluster_cert)
    try_set_file_permissions(cluster_cert_file)
    with open(cluster_key_file, 'w+') as fp:
        fp.write(cluster_key)
    try_set_file_permissions(cluster_key_file)


def store_front_proxy_certs(cert, cert_key):
    cert_file = "{}/certs/front-proxy-client.crt".format(snapdata_path)
    cert_key_file = "{}/certs/front-proxy-client.key".format(snapdata_path)
    with open(cert_file, 'w+') as fp:
        fp.write(cert)
    try_set_file_permissions(cert_file)
    with open(cert_key_file, 'w+') as fp:
        fp.write(cert_key)
    try_set_file_permissions(cert_key_file)


def store_base_kubelet_args(args_string):
    """
    Create a kubelet args file from the set of args provided

    :param args_string: the arguments provided
    """
    args_file = "{}/args/kubelet".format(snapdata_path)
    with open(args_file, "w") as fp:
        fp.write(args_string)
    try_set_file_permissions(args_file)


def store_callback_token(token):
    callback_token_file = "{}/credentials/callback-token.txt".format(snapdata_path)
    with open(callback_token_file, "w") as fp:
        fp.write(token)
    try_set_file_permissions(callback_token_file)


def reset_current_installation():
    """
    Take a node out of a cluster
    """
    lock_file = "{}/var/lock/clustered.lock".format(snapdata_path)
    if not os.path.isfile(lock_file):
        print("Not in clustering mode.")
        exit(2)

    os.remove(lock_file)
    os.remove(ca_cert_file)
    os.remove(callback_token_file)
    os.remove(server_cert_file)

    for config_file in ["kubelet", "kube-proxy"]:
        shutil.copyfile("{}/default-args/{}".format(snap_path, config_file),
                        "{}/args/{}".format(snapdata_path, config_file))

    for user in ["proxy", "kubelet"]:
        config = "{}/credentials/{}.config".format(snapdata_path, user)
        shutil.copyfile("{}.backup".format(config), config)

    subprocess.check_call("{}/microk8s-stop.wrapper".format(snap_path).split())
    waits = 10
    while waits > 0:
        try:
            subprocess.check_call("{}/microk8s-start.wrapper".format(snap_path).split())
            break
        except subprocess.CalledProcessError:
            print("Services not ready to start. Waiting...")
            time.sleep(5)
            waits -= 1


def remove_kubelet_token(node):
    """
    Remove a token for a node in the known tokens

    :param node: the name of the node
    """
    file = "{}/credentials/known_tokens.csv".format(snapdata_path)
    backup_file = "{}.backup".format(file)
    token = "system:node:{}".format(node)
    # That is a critical section. We need to protect it.
    with open(backup_file, 'w') as back_fp:
        with open(file, 'r') as fp:
            for _, line in enumerate(fp):
                if token in line:
                    continue
                back_fp.write("{}".format(line))

    try_set_file_permissions(backup_file)
    shutil.copyfile(backup_file, file)


def remove_callback_token(node):
    """
    Remove a callback token

    :param node: the node
    """
    tmp_file = "{}.tmp".format(callback_tokens_file)
    if not os.path.isfile(callback_tokens_file):
        open(callback_tokens_file, 'a+')
        os.chmod(callback_tokens_file, 0o600)
    with open(tmp_file, "w") as backup_fp:
        os.chmod(tmp_file, 0o600)
        with open(callback_tokens_file, 'r+') as callback_fp:
            for _, line in enumerate(callback_fp):
                if line.startswith(node):
                    continue
                else:
                    backup_fp.write(line)

    try_set_file_permissions(tmp_file)
    shutil.move(tmp_file, callback_tokens_file)


def remove_node(node):
    try:
        # Make sure this node exists
        subprocess.check_call("{}/microk8s-kubectl.wrapper get no {}".format(snap_path, node).split(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("Node {} does not exist.".format(node))
        exit(1)

    remove_kubelet_token(node)
    remove_callback_token(node)
    subprocess.check_call("{}/microk8s-kubectl.wrapper delete no {}".format(snap_path, node).split(),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# TODO eliminate duplicata code
# TODO check we do not have a bug in the other get_token (no for loop)
def get_token(name, tokens_file="known_tokens.csv"):
    """
    Get token from known_tokens file

    :param name: the name of the node
    :returns: the token or None(if name doesn't exist)
    """
    file = "{}/credentials/{}".format(snapdata_path, tokens_file)
    with open(file) as fp:
        for line in fp:
            if name in line:
                parts = line.split(',')
                return parts[0].rstrip()
    return None


def update_dqlite(cluster_cert, cluster_key, clusrt_ip, cluster_port):
    subprocess.check_call("systemctl stop snap.microk8s.daemon-apiserver.service".split())
    time.sleep(10)
    shutil.rmtree(cluster_backup_dir, ignore_errors=True)
    shutil.move(cluster_dir, cluster_backup_dir)
    os.mkdir(cluster_dir)
    store_cluster_certs(cluster_cert, cluster_key)
    with open("{}/info.yaml".format(cluster_backup_dir)) as f:
        data = yaml.load(f, Loader=yaml.FullLoader)

    init_data = {'Cluster': ['{}:{}'.format(clusrt_ip, cluster_port)], 'Address': data['Address']}
    with open("{}/init.yaml".format(cluster_dir), 'w') as f:
        yaml.dump(init_data, f)

    subprocess.check_call("systemctl start snap.microk8s.daemon-apiserver.service".split())

    waits = 10
    print("Waiting for node to join the cluster.", end=" ", flush=True)
    while waits > 0:
        try:
            out = subprocess.check_output("curl https://{}/cluster --cacert {} --key {} --cert {} -k -s"
                                    .format(data['Address'], cluster_cert_file, cluster_key_file,
                                            cluster_cert_file).split());
            if data['Address'] in out.decode():
                break
            else:
                print(".", end=" ", flush=True)
                time.sleep(5)
                waits -= 1

        except subprocess.CalledProcessError:
            print("..", end=" ", flush=True)
            time.sleep(5)
            waits -= 1
    print(" ")

    subprocess.check_call("{}/microk8s-stop.wrapper".format(snap_path).split())
    waits = 10
    while waits > 0:
        try:
            subprocess.check_call("{}/microk8s-start.wrapper".format(snap_path).split())
            break
        except subprocess.CalledProcessError:
            time.sleep(5)
            waits -= 1


if __name__ == "__main__":
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ["help"])
    except getopt.GetoptError as err:
        print(err)  # will print something like "option -a not recognized"
        usage()
        sys.exit(2)
    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit(1)
        else:
            print("Unhandled option")
            sys.exit(1)

    if args[0] == "reset":
        if len(args) > 1:
            remove_node(args[1])
        else:
            reset_current_installation()
    else:
        if len(args) <= 0:
            print("Please provide a connection string.")
            usage()
            sys.exit(4)

        connection_parts = args[0].split("/")
        token = connection_parts[1]
        master_ep = connection_parts[0].split(":")
        master_ip = master_ep[0]
        master_port = master_ep[1]
        info = get_connection_info(master_ip, master_port, token)

        if "cluster_key" not in info:
            print("The cluster you are attempting to join is incompatible with the current MicroK8s instance.")
            print("Please, either reinstall the node from a pre v1.18 track with "
                  "(sudo snap install microk8s --classic --channel=1.17/stable) "
                  "or update the cluster to a version newer than v1.17.")
            sys.exit(5)

        hostname_override = None
        if 'hostname_override' in info:
            hostname_override = info['hostname_override']

        store_remote_ca(info["ca"], info["ca_key"])
        store_remote_server_cert(info["server_cert"], info["server_cert_key"])
        store_remote_service_account_key(info["service_account_key"])
        store_front_proxy_certs(info["proxy_cert"], info["proxy_cert_key"])
        # triplets of [username in known_tokens.csv, username in kubeconfig, kubeconfig filename name]
        for component in [("kube-proxy", "kubeproxy", "proxy.config"),
                          ("kubelet", "kubelet", "kubelet.config"),
                          ("kube-controller-manager", "controller", "controller.config"),
                          ("kube-scheduler", "scheduler", "scheduler.config")]:
            token = get_token(component[0])
            # TODO make this configurable
            create_kubeconfig(token, info["ca"], "127.0.0.1", "16443", component[2], component[1])
        create_admin_kubeconfig(info["ca"])
        store_base_kubelet_args(info["kubelet_args"])
        store_callback_token(info["callback_token"])

        update_dqlite(info["cluster_cert"], info["cluster_key"], master_ip, info["cluster_port"])

    sys.exit(0)
