#!/usr/bin/env bash

set -e

source $SNAP/actions/common/utils.sh

echo "Enabling RBAC"

echo "Reconfiguring apiserver"
refresh_opt_in_config "authorization-mode" "RBAC,Node" kube-apiserver
sudo snapctl restart ${SNAP_NAME}.daemon-apiserver

echo "RBAC is enabled"
