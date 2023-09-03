# pulumi-azure
This repository holds Pulumi stacks for Azure

## Getting started

### Prerequisites
---
#### Language runtime

Here we'll use Python as language runtime for Pulumi so ensure that `python`, `pip` and `poetry` are installed.

#### Install Pulumi
On MacOS
```bash
brew install pulumi/tap/pulumi
```

On Linux
```bash
curl -fsSL https://get.pulumi.com | sh
```

#### Authentication
To allow Pulumi to access Microsoft Azure account you have two options :
- Login with a user account (a.k.a `az login`)
- Login with a Service Principal (a.k.a service account)

> Pulumi relies on the Azure SDK to authenticate requests. Credentials are never sent to pulumi.com.

Here we will login using Azure CLI :
```bash
# This will open a browser windows
az login
```

You also need a Pulumi account :
```bash
pulumi login
```

After successfully logging in, you are ready to go.
