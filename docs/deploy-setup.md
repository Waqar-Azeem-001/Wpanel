# Deploying from GitHub with an approval, as a user who can do almost nothing

The CI sends the code and runs the deploy over SSH. This page sets the server up so that the SSH key it uses **cannot do anything else**, and so that the deploy only starts after you approve it on GitHub.

What each part does:

| Part | Who | What it can do |
|---|---|---|
| `deploy` key (GitHub environment secret `DEPLOY_SSH_KEY`) | the CI deploy job, after your approval | send a tar file of the code and ask for a deploy. Nothing else |
| `notify` key (repository secret `NOTIFY_SSH_KEY`) | the CI notify job, no approval needed | send the team a notice ("tests passed / failed, deploy waiting"). Cannot deploy |
| `whpanel-deploy` (installed in `/usr/local/sbin`, owned by root) | run through `sudo` by the deploy entry | backup, copy, rebuild, health check, warm-up (`deploy/server/whpanel-deploy`) |

The two users are **not** in the `docker` group (that group is the same as root). They can only run the two root-owned scripts through `sudo`.

## 1. On the server, as root

Run these from a checkout of the repository on the server, or copy the four files in `deploy/server/` over first.

```bash
# the two users and the folder the upload goes into
adduser --disabled-password --gecos "" deploy
adduser --disabled-password --gecos "" notify
chown deploy:deploy /srv/whpanel.incoming && chmod 750 /srv/whpanel.incoming

# the scripts: owned by root, so the two users cannot change what runs as root
install -o root -g root -m 755 deploy/server/whpanel-deploy       /usr/local/sbin/whpanel-deploy
install -o root -g root -m 755 deploy/server/whpanel-notify       /usr/local/sbin/whpanel-notify
install -o root -g root -m 755 deploy/server/whpanel-deploy-entry /usr/local/bin/whpanel-deploy-entry
install -o root -g root -m 755 deploy/server/whpanel-notify-entry /usr/local/bin/whpanel-notify-entry

# sudo: each user may run exactly one script
cat > /etc/sudoers.d/whpanel-deploy <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/local/sbin/whpanel-deploy
notify ALL=(root) NOPASSWD: /usr/local/sbin/whpanel-notify
EOF
chmod 440 /etc/sudoers.d/whpanel-deploy
visudo -c -f /etc/sudoers.d/whpanel-deploy
```

When the deploy script itself changes in the repository, root installs the new copy by hand (the `install` line above). The CI cannot change what runs as root; that is the point.

## 2. Two keys, made on your own computer

```bash
ssh-keygen -t ed25519 -f whpanel-ci-deploy -C "ci-deploy" -N ""
ssh-keygen -t ed25519 -f whpanel-ci-notify -C "ci-notify" -N ""
```

Keep the two private files (`whpanel-ci-deploy`, `whpanel-ci-notify`) for step 4. Never put them in the repository.

## 3. On the server: the restricted `authorized_keys`

`restrict` turns off the terminal, port forwarding, agent forwarding and X11. `command=` makes the server run the entry script **whatever the client asks for**; the entry script only accepts `deploy [commit]` (or `notify ...`).

```bash
for u in deploy notify; do install -d -m 700 -o $u -g $u /home/$u/.ssh; done

# paste the contents of whpanel-ci-deploy.pub / whpanel-ci-notify.pub where it says AAAA...
echo 'restrict,command="/usr/local/bin/whpanel-deploy-entry" ssh-ed25519 AAAA... ci-deploy' > /home/deploy/.ssh/authorized_keys
echo 'restrict,command="/usr/local/bin/whpanel-notify-entry" ssh-ed25519 AAAA... ci-notify' > /home/notify/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys; chown notify:notify /home/notify/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys /home/notify/.ssh/authorized_keys
```

(The server already allows key logins only. Check with `sshd -T | grep -E "passwordauthentication|permitrootlogin"`.)

## 4. Try it from your computer before touching GitHub

```bash
# should refuse (these keys run nothing but their one command):
ssh -i whpanel-ci-deploy deploy@62.238.96.50 "ls /"          # -> this key may only run: deploy [commit]
ssh -i whpanel-ci-notify notify@62.238.96.50 "deploy"         # -> this key may only run: notify ...
ssh -i whpanel-ci-deploy deploy@62.238.96.50                  # no shell is given

# a real deploy of the current commit (backup, rebuild, warm-up, all on the server):
git archive HEAD | ssh -i whpanel-ci-deploy deploy@62.238.96.50 "deploy $(git rev-parse HEAD)"

# a notice:
ssh -i whpanel-ci-notify notify@62.238.96.50 "notify --tests success --deploy 'waiting for your approval' --sha $(git rev-parse HEAD) --actor me --message test"
```

## 5. On GitHub (you do this)

1. **Settings > Environments > New environment: `production`.**
   - *Required reviewers*: add yourself. Leave *Prevent self-review* **off** (you are the only reviewer and also the one who pushes).
   - *Deployment branches*: **Selected branches: `main`** only.
   - *Environment secrets*: add `DEPLOY_SSH_KEY` = the whole contents of `whpanel-ci-deploy` (the private key). Because it is an *environment* secret, no job can read it until you approve.
2. **Settings > Secrets and variables > Actions > Repository secrets**: add `NOTIFY_SSH_KEY` = the contents of `whpanel-ci-notify`.
3. Delete the old repository secret `DEPLOY_SSH_KEY` if one exists (it would have been the root key).

After that: a push to `main` runs the tests and the security scan; the **notify** job emails the team at once ("tests passed, deploy waiting for your approval"); the **deploy** job waits on GitHub for you to press *Review deployments > Approve*; then it sends the code and the server deploys it and emails "Deployed `<commit>`" (or "Deploy failed").

## What this does not protect against

The deploy builds the Docker image from the code it is given, and the build runs as root on the server. So whoever can both push to `main` **and** approve the deployment can run anything on the server. The approval is the protection; keep it on, and keep branch protection on `main` (no direct pushes, no force pushes).

## If something goes wrong

- The deploy script takes a database backup first: `/srv/backups/predeploy-<time>.dump`. Restore it from the panel (Setup > Storage and backups) or with `pg_restore`.
- To go back to the previous code, deploy the previous commit the same way.
- To stop CI deploying at all: remove the `DEPLOY_SSH_KEY` environment secret (the deploy job then runs and says "NOT DEPLOYED") or delete the line in `/home/deploy/.ssh/authorized_keys`.
