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

## Connect your MCP client

You will need to give your MCP client the URL of the dbsliceAI MCP server. The
examples below use `https://app.ai.dbslice.org`, but use a different one if
your administrator has specified it.

### Claude

Add dbsliceAI as a **custom connector**:

1. Open **Settings → Connectors**.
2. Select **Add custom connector**.
3. Name it `dbsliceAI` and enter the hosted MCP server URL, for example
   `https://app.ai.dbslice.org/mcp`.
4. Connect and sign in with your invited account.

### Claude Code

Add dbsliceAI to the current project, then start the OAuth login:

```bash
claude mcp add --transport http dbsliceAI https://app.ai.dbslice.org/mcp
claude mcp login dbsliceAI
```

If `claude mcp login` is unavailable, update Claude Code or start an
interactive `claude` session, enter `/mcp`, select `dbsliceAI` and follow the
browser login flow.

Use `claude mcp list` to verify the connection. Run local-scope commands from
the project directory where you want dbsliceAI to be available.

See the [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
for installation scopes and OAuth commands.

### ChatGPT

Add dbsliceAI as a **custom plugin**:

1. Open **Settings → Security and login** and enable **Developer mode**.
2. Open **Plugins** and select **+**.
3. Name the custom plugin `dbsliceAI` and enter the hosted MCP server URL, for
   example `https://app.ai.dbslice.org/mcp`.
4. Connect and sign in with your invited account.
5. Start a new chat with the custom plugin enabled.

### Codex CLI

dbsliceAI uses Client ID Metadata Documents (CIMD) for Codex authorization.
Use a Codex CLI version whose `codex mcp add --help` output includes
`--oauth-client-registration`; this flow has been verified with
`codex-cli 0.148.0-alpha.9`.

If necessary, install this version:

```bash
npm install -g @openai/codex@0.148.0-alpha.9
```

Add dbsliceAI and select CIMD explicitly:

```bash
codex mcp add dbsliceAI --url https://app.ai.dbslice.org/mcp --oauth-client-registration cimd
```

Authenticate with the scopes required by dbsliceAI:

```bash
codex mcp login dbsliceAI --scopes openid,profile,email,offline_access,mcp:use --oauth-client-registration cimd
```

Use `codex mcp list` to verify the saved server. Start a new Codex session and
enter `/mcp verbose` to inspect the connection and available tools. See the
[Codex command reference](https://learn.chatgpt.com/docs/developer-commands#codex-mcp)
for the current MCP management commands.

### Other MCP clients

dbsliceAI uses controlled OAuth client registration. If a CLI reports
`Unknown client`, send the exact OAuth `client_id` or CIMD URL shown in the
error to the dbsliceAI administrator. Each distinct client application must
be approved once; individual users and installations do not require separate
client registrations.

Once connected, try this prompt:

> Teach me how to use the dbsliceAI tools. Use an available dataset to give a
> short example of each tool, including useful plots.

You can begin with datasets already provided by the hosted service. To make a
dataset on your own computer available, install and run the connector below.

## Install the connector

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
  --dataset ~/datasets/dbslice-ai-sample-data-1.1.0
```

Leave the connector running while you use the dataset in dbsliceAI. Stop it
with **Ctrl-C**.

## Connect your own dataset

Supply the directory containing the dataset's `config/config.json` file:

```bash
dbslice-ai-connector run --dataset /absolute/path/to/dataset
```

The dataset title is read from its configuration. The connector automatically
reconnects after temporary network interruptions. If the dataset declares a
linked curated-reference manifest, its citations, summaries and web links are
made available automatically. Local paths and documents are never shared.

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
