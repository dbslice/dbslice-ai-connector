# dbsliceAI connector

Make a dataset on your computer available in dbsliceAI.

Learn more at the [dbsliceAI website](https://ai.dbslice.org/).

The connector reads only the dataset directory you select and makes an
outbound connection to dbsliceAI. You do not need to open an inbound port or
run a public file server, and local file paths are not shared with dbsliceAI
clients.

## Before you begin

New users must first be invited by the administrator of the hosted dbsliceAI
service they will use. The administrator supplies the invitation and the
service URL. Accept the invitation before following the steps below.

After your account is ready, you pair each computer yourself. The administrator
does not need to create a connector or send you a connector-specific secret.

## Install

The connector requires Python 3.11 or newer. Install it from PyPI:

```bash
python3 -m pip install dbslice-ai-connector
```

## Connect this computer

Pair the connector with your dbsliceAI account. Use the service URL supplied
by your dbsliceAI provider. For example:

```bash
dbslice-ai-connector pair --server-url https://app.ai.dbslice.org
```

The command opens a secure page in your browser. Sign in and select
**Connect device**. If the browser is on another computer, add
`--no-open-browser` and open the printed URL there.

Pairing is required only once on each computer.

## Download the sample dataset

To try the connector with the dbsliceAI sample dataset:

```bash
dbslice-ai-connector download-sample --destination ~/datasets
```

The connector downloads the sample, verifies it and prints its full path and
the command to use next. It will not replace an existing copy.

Connect the downloaded dataset in exactly the same way as any other dataset:

```bash
dbslice-ai-connector run \
  --dataset ~/datasets/dbslice-ai-sample-data-1.0.0
```

Leave the connector running while you use the dataset in dbsliceAI. Stop it
with **Ctrl-C**.

## Connect your own dataset

Supply the directory containing the dataset's `config/config.json` file:

```bash
dbslice-ai-connector run --dataset /absolute/path/to/dataset
```

The dataset title is read from its configuration. The connector automatically
reconnects after temporary network interruptions.

The complete directory format is described in the
[dbsliceAI dataset specification](https://github.com/dbslice/dbslice-ai-sample-data/blob/main/DATASET_FORMAT.md).

## Troubleshooting

If a Python.org installation on macOS reports `CERTIFICATE_VERIFY_FAILED`, run
the `Install Certificates.command` supplied with that Python installation,
then try again. Do not disable certificate verification.

Use `dbslice-ai-connector --help` or
`dbslice-ai-connector <command> --help` to see all available options.

The connector stores its paired identity privately on the computer. Do not
include credentials, pairing codes or private dataset contents in bug reports.
