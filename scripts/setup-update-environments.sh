#!/bin/bash
# Create the GitHub environments the update pipeline signs and promotes in.
#
#   scripts/setup-update-environments.sh --reviewer LOGIN [--reviewer LOGIN] [--apply]
#
# Without --apply it only prints what it would do. It never reads, writes or
# deletes secret values: moving each key into its environment is a manual step
# printed at the end, because only the key holder has the private material.
#
# Environments (deployment branch: main only):
#   testing-signing   distro repos   package/cache keys; no reviewers (automation)
#   stable-promotion  distro repos   required reviewers; gates stable promotion and rollbacks
#   catalog-signing   all four repos installer catalog key
#   source-tracking   distro repos   optional GitHub App key for the source tracker
set -euo pipefail
org=aurora-silicon
distros=(alarm-aurora fedora-aurora nixos-aurora)
reviewers=()
apply=false
while (($#)); do
  case $1 in
    --reviewer) reviewers+=("$2"); shift 2 ;;
    --apply) apply=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
((${#reviewers[@]})) || { echo 'at least one --reviewer LOGIN is required for stable-promotion' >&2; exit 2; }

reviewer_json=$(for login in "${reviewers[@]}"; do
  id=$(gh api "users/$login" --jq .id)
  printf '{"type":"User","id":%s}\n' "$id"
done | jq -s .)

environment() {
  local repo=$1 name=$2 with_reviewers=$3 body
  if [[ $with_reviewers == yes ]]; then
    body=$(jq -n --argjson reviewers "$reviewer_json" \
      '{wait_timer: 0, prevent_self_review: false, reviewers: $reviewers,
        deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}}')
  else
    body='{"wait_timer":0,"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
  fi
  echo "== $org/$repo: environment $name (reviewers: $with_reviewers, branch: main)"
  if [[ $apply == true ]]; then
    gh api --method PUT "repos/$org/$repo/environments/$name" --input - <<< "$body" >/dev/null
    existing=$(gh api "repos/$org/$repo/environments/$name/deployment-branch-policies" \
      --jq '[.branch_policies[].name] | join(" ")')
    if [[ " $existing " != *" main "* ]]; then
      gh api --method POST "repos/$org/$repo/environments/$name/deployment-branch-policies" \
        -f name=main -f type=branch >/dev/null
    fi
  fi
}

for repo in "${distros[@]}"; do
  environment "$repo" testing-signing no
  environment "$repo" stable-promotion yes
  environment "$repo" catalog-signing no
  environment "$repo" source-tracking no
done
environment aurora-distributions catalog-signing no

cat <<'EOF'

Next, by hand (the private keys never pass through this script):

  # ALARM
  gh secret set ALARM_SIGNING_KEY        --env testing-signing -R aurora-silicon/alarm-aurora  < alarm-signing-key.asc
  gh secret set ALARM_SIGNING_PASSPHRASE --env testing-signing -R aurora-silicon/alarm-aurora
  # Fedora
  gh secret set FEDORA_RPM_SIGNING_KEY        --env testing-signing -R aurora-silicon/fedora-aurora < rpm-signing-key.asc
  gh secret set FEDORA_RPM_SIGNING_PASSPHRASE --env testing-signing -R aurora-silicon/fedora-aurora
  # NixOS
  gh secret set NIX_CACHE_SIGNING_KEY --env testing-signing -R aurora-silicon/nixos-aurora < nix-cache.secret
  # Installer catalogs (all four repositories)
  for r in alarm-aurora fedora-aurora nixos-aurora aurora-distributions; do
    gh secret set INSTALLER_CATALOG_SIGNING_KEY --env catalog-signing -R aurora-silicon/$r < catalog-key.pem
  done

The source tracker opens its PR with the workflow token, which these
repositories do not allow yet. Either allow it (Settings > Actions > General >
"Allow GitHub Actions to create and approve pull requests"), or, preferred,
install a GitHub App with Contents, Pull requests and Actions write on the three
distribution repositories and store it:

  gh variable set TRACKER_APP_CLIENT_ID --body <client id> -R aurora-silicon/<repo>
  gh secret set TRACKER_APP_PRIVATE_KEY --env source-tracking -R aurora-silicon/<repo> < app.private-key.pem

Then confirm one signing run per repository succeeds, and only then delete the
repository-level copies, which any workflow on any branch can read:

  gh secret delete ALARM_SIGNING_KEY -R aurora-silicon/alarm-aurora   # and the others above

Automation switches (repository variables, all off until set to "true"):

  gh variable set TRACK_SOURCES        --body true -R aurora-silicon/<repo>  # open/refresh pin PRs every 30 min
  gh variable set AUTO_MERGE_SOURCES   --body true -R aurora-silicon/<repo>  # merge a pin PR once its CI passes
  gh variable set AUTO_PUBLISH_TESTING --body true -R aurora-silicon/<repo>  # signed snapshot after each main build
  gh variable set AUTO_DEPLOY_TESTING  --body true -R aurora-silicon/<repo>  # make that snapshot the live testing feed
  gh variable set AUTO_IMAGES          --body true -R aurora-silicon/<repo>  # rebuild images after a testing deploy
  gh variable set AUTO_PAYLOADS        --body true -R aurora-silicon/<repo>  # payload release after each image build
  gh variable set STABLE_MIN_SOAK_HOURS --body 24 -R aurora-silicon/<repo> # minimum time on testing before stable
EOF
[[ $apply == true ]] || echo $'\nDry run: nothing was changed. Re-run with --apply.'
