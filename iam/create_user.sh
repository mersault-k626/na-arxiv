#!/usr/bin/env bash
set -e

# aws iam create-user --generate-cli-skeleton yaml-input > create_user.yaml
# aws iam attach-user-policy --generate-cli-skeleton yaml-input > attach_policy.yaml


# create user, then save it as yaml
aws iam create-user --cli-input-yaml file://create_user.yaml --output yaml > user_cred.yaml

# acc id constant
USER_NAME=$(yq '.UserName' create_user.yaml)
ACCOUNT_ID=$(yq '.User.Arn' user_cred.yaml | cut -d: -f5)

# create policy for arxiv
aws iam create-policy --cli-input-yaml file://create_policy.yaml



# attach policy based on yaml (UserName/PolicyArn overridden dynamically, not written to file)
aws iam attach-user-policy --user-name "$USER_NAME" --policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/arvix-db-full-access"

# qc; dump users' attached policies
aws iam list-attached-user-policies --user-name "$USER_NAME" --output yaml > user_policies_check.yaml

# generate keys
aws iam create-access-key --user-name "$USER_NAME" --output yaml > access_key.yaml

aws iam list-users