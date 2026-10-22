#!/usr/bin/env bash
# One-time AWS setup for the OMR Lambda (see README.md beside this file).
# Run it in AWS CloudShell -- the >_ icon in the AWS console's top bar --
# with the console switched to the region the server is in:
#
#   bash setup-aws.sh i-0123456789abcdef0     # the server's instance ID
#
# It is re-runnable: anything that already exists is left as it is. It
# creates, and touches nothing else:
#   * an ECR repository `music-omr` for the image (keeping the last 3),
#   * the Lambda's own role `music-omr-lambda` (may write its logs, nothing else),
#   * a role `music-omr-deploy` that GitHub Actions on this repo's main
#     branch -- and nothing else -- can assume, to push the image and
#     update the function, plus the GitHub OIDC provider that makes that
#     possible without any stored keys,
#   * permission for the server to invoke the function: an inline policy on
#     the instance's role, creating a role and attaching it if it has none.
set -euo pipefail

GITHUB_REPO=nicklitvin/music
FUNCTION=music-omr
INSTANCE_ID=${1:-}

REGION=${AWS_REGION:-$(aws configure get region || true)}
[ -n "$REGION" ] || { echo "No region: set AWS_REGION, e.g. AWS_REGION=us-east-2 bash $0 ..." >&2; exit 1; }
export AWS_REGION=$REGION AWS_PAGER=""
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
FUNCTION_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNCTION"
REPO_ARN="arn:aws:ecr:$REGION:$ACCOUNT:repository/$FUNCTION"
OIDC_ARN="arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
say() { printf '\n== %s\n' "$*"; }

if [ -z "$INSTANCE_ID" ]; then
  ids=$(aws ec2 describe-instances --filters Name=instance-state-name,Values=running,stopped \
        --query 'Reservations[].Instances[].InstanceId' --output text)
  if [ "$(wc -w <<<"$ids")" -ne 1 ]; then
    echo "Pass the server's instance ID (found: ${ids:-none} in $REGION)." >&2
    exit 1
  fi
  INSTANCE_ID=$ids
fi
say "account $ACCOUNT, region $REGION, server $INSTANCE_ID"

# --- ECR ---------------------------------------------------------------
say "image repository $FUNCTION"
if ! aws ecr describe-repositories --repository-names "$FUNCTION" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$FUNCTION" >/dev/null
  echo "created"
fi
cat >"$TMP/lifecycle.json" <<'EOF'
{"rules": [{"rulePriority": 1, "description": "keep the last 3 images",
  "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 3},
  "action": {"type": "expire"}}]}
EOF
aws ecr put-lifecycle-policy --repository-name "$FUNCTION" \
  --lifecycle-policy-text "file://$TMP/lifecycle.json" >/dev/null
# Lambda pulls the image with its own service principal.
cat >"$TMP/repo-policy.json" <<EOF
{"Version": "2012-10-17", "Statement": [{"Sid": "LambdaPull", "Effect": "Allow",
  "Principal": {"Service": "lambda.amazonaws.com"},
  "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
  "Condition": {"StringLike": {"aws:sourceArn": "arn:aws:lambda:$REGION:$ACCOUNT:function:*"}}}]}
EOF
aws ecr set-repository-policy --repository-name "$FUNCTION" \
  --policy-text "file://$TMP/repo-policy.json" >/dev/null

ensure_role() {  # name, trust policy file
  if ! aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    aws iam create-role --role-name "$1" --assume-role-policy-document "file://$2" >/dev/null
    echo "created role $1"
  else
    aws iam update-assume-role-policy --role-name "$1" --policy-document "file://$2"
  fi
}

# --- The Lambda's role -------------------------------------------------
say "Lambda role music-omr-lambda"
cat >"$TMP/lambda-trust.json" <<'EOF'
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
EOF
ensure_role music-omr-lambda "$TMP/lambda-trust.json"
aws iam attach-role-policy --role-name music-omr-lambda \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# --- GitHub Actions' role ------------------------------------------------
say "GitHub Actions role music-omr-deploy (only $GITHUB_REPO, main branch)"
if ! aws iam list-open-id-connect-providers --output text | grep -q "token.actions.githubusercontent.com"; then
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
  echo "created the GitHub OIDC provider"
fi
cat >"$TMP/deploy-trust.json" <<EOF
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Federated": "$OIDC_ARN"}, "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {"StringEquals": {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub": "repo:$GITHUB_REPO:ref:refs/heads/main"}}}]}
EOF
ensure_role music-omr-deploy "$TMP/deploy-trust.json"
cat >"$TMP/deploy-policy.json" <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
  {"Effect": "Allow", "Resource": "$REPO_ARN", "Action": [
    "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer", "ecr:GetRepositoryPolicy", "ecr:SetRepositoryPolicy"]},
  {"Effect": "Allow", "Resource": "$FUNCTION_ARN", "Action": [
    "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:CreateFunction",
    "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:InvokeFunction"]},
  {"Effect": "Allow", "Action": "iam:PassRole",
   "Resource": "arn:aws:iam::$ACCOUNT:role/music-omr-lambda",
   "Condition": {"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}}}]}
EOF
aws iam put-role-policy --role-name music-omr-deploy --policy-name deploy-music-omr \
  --policy-document "file://$TMP/deploy-policy.json"

# --- The server --------------------------------------------------------
say "letting the server invoke $FUNCTION"
profile_arn=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].IamInstanceProfile.Arn' --output text)
if [ "$profile_arn" = "None" ] || [ -z "$profile_arn" ]; then
  cat >"$TMP/ec2-trust.json" <<'EOF'
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
EOF
  ensure_role music-backend "$TMP/ec2-trust.json"
  if ! aws iam get-instance-profile --instance-profile-name music-backend >/dev/null 2>&1; then
    aws iam create-instance-profile --instance-profile-name music-backend >/dev/null
    aws iam add-role-to-instance-profile --instance-profile-name music-backend --role-name music-backend
    sleep 10  # a brand-new instance profile takes a moment to be usable
  fi
  aws ec2 associate-iam-instance-profile --instance-id "$INSTANCE_ID" \
    --iam-instance-profile Name=music-backend >/dev/null
  echo "gave the server the role music-backend"
  server_role=music-backend
else
  server_role=$(aws iam get-instance-profile --instance-profile-name "${profile_arn##*/}" \
    --query 'InstanceProfile.Roles[0].RoleName' --output text)
  echo "the server already has the role $server_role; adding to it"
fi
cat >"$TMP/invoke-policy.json" <<EOF
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Action": "lambda:InvokeFunction", "Resource": "$FUNCTION_ARN"}]}
EOF
aws iam put-role-policy --role-name "$server_role" --policy-name invoke-music-omr \
  --policy-document "file://$TMP/invoke-policy.json"

limit=$(aws lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text)

say "done"
cat <<EOF

Now, in GitHub: Settings -> Secrets and variables -> Actions -> Variables, add
  AWS_DEPLOY_ROLE_ARN = arn:aws:iam::$ACCOUNT:role/music-omr-deploy
  AWS_REGION          = $REGION
then run the "OMR Lambda" workflow from the Actions tab.

This account can run $limit Lambdas at once (pages beyond that wait their turn).
EOF
