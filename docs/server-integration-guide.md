# Connecting a real server (WHM / cPanel) to WHPanel

For the owner and the technical staff. Nothing here is done by the code alone: it needs your server and its WHM login.

## What WHPanel needs from a server
WHPanel creates, suspends, unsuspends, changes the plan of, backs up and terminates hosting accounts by calling the server's
**WHM API** (port 2087). So the server must run **cPanel & WHM** (a cPanel licence is needed; a bare VPS from Contabo or
anyone else is not enough until cPanel is installed on it).

Contabo's own **client id / client secret / API user / API password** are for Contabo's API (restart, reinstall or snapshot the
VPS). WHPanel does not use them to create hosting accounts; they are stored on the server only so staff can check that the
machine, its IP address and its status are what they think (see "The provider login" below).

## One-time steps on the server (WHM)
1. Install cPanel & WHM on the VPS and open `https://<server>:2087` (use the hostname, e.g. `northhillstudios.com`).
2. Make sure the hostname has a valid certificate, or plan to switch off "Verify SSL" for a self-signed one (not recommended).
3. In WHM go to **Development > Manage API Tokens**, create a token, and copy it once (WHM never shows it again).
   Use the **root** user, or a reseller that is allowed to create accounts. A normal cPanel user cannot.
4. Create the hosting **packages** in WHM (**Packages > Add a Package**), e.g. `starter`, `business`. Note their exact names.
5. Open port 2087 for the machine that runs WHPanel (firewall / security group).

## In WHPanel (staff area > Servers)
1. **Edit the server:** hostname, the server's **IP address**, kind **WHM API**, port **2087**, API username (`root`, or the
   reseller), paste the **API token**, keep "Use SSL" and "Verify SSL" on. Save.
2. Press **Test connection** (on the Servers list, or on the server's edit page). It reads the WHM version and the packages
   without changing anything. A refused login says so plainly; a product whose package name the server does not have is warned about.
3. **Products:** on each hosting product set **WHM package name** to the exact package name from step 4, and tick this server
   under the product's servers.
4. **Try one real account** with a throwaway domain: order or add it, accept the order, and check the account appears in WHM;
   then try Suspend, Unsuspend and Terminate on it.
5. Only after that, turn on the scheduled usage sync (`HOSTING_USAGE_SYNC_ENABLED=true`).

## The provider login (Contabo client ID and secret)
In Contabo's Customer Control Panel open **API** and create (or copy) the four details. In WHPanel open **Servers > Edit**:

| Contabo calls it | WHPanel field |
|---|---|
| Client ID | Provider client ID |
| Client Secret | Provider client secret |
| API User (the e-mail address) | Provider API user |
| API Password (the one you set under API) | Provider API password |

Choose **Provider (who rents you this machine) = Contabo**, save, then press **Test the provider login**. It signs in to
Contabo, lists the machines that login owns (name, IP address, status, region) and tells you whether one has this server's IP
address; if no IP is saved and there is only one machine it tells you which IP to enter. It is read-only: nothing is created,
restarted or changed. The two secrets are stored encrypted and are never shown again; leave them blank when editing to keep them.
The two Contabo web addresses live in settings (`CONTABO_AUTH_URL`, `CONTABO_API_URL`), not in the code.

## If Test connection fails
| Message | Usually means |
|---|---|
| "The server refused the login" | wrong API username or token, or the token lacks privileges |
| "Could not reach the server" | wrong hostname or port, firewall, or WHM is not running |
| certificate error | the certificate does not match the hostname; fix it, or switch off "Verify SSL" for a trusted self-signed one |
| "Token denied" and similar WHM text | the WHM user may not use that function |

## Not built yet
Acting on the machine from WHPanel (restart, reinstall, snapshots: the infrastructure dashboard, H13), and creating the WHM
token automatically.
