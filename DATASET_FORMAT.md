# Dataset format

A dataset is a directory containing:

- one configuration file
- one JSON file listing the items
- any images, line data, 3D models or embeddings associated with those items

The connector reads only from the dataset directory supplied with `--dataset`.
The directory can be placed anywhere on the machine.

## Minimal example

```text
example-dataset/
├── config/
│   └── config.json
└── data/
    ├── metadata/
    │   └── items.json
    └── extracts/
        └── image-extract/
            ├── item_1.png
            └── item_2.png
```

`config/config.json`:

```json
{
  "dataset": {
    "title": "Example study"
  },
  "metaData": {
    "path": "data/metadata/items.json",
    "config": {
      "title": "Example study",
      "description": "Two example cases"
    }
  },
  "extracts": [
    {
      "extractId": "image-extract",
      "type": "image",
      "description": "An image for each item",
      "format": "png",
      "path": "data/extracts/image-extract/${itemId}.png"
    }
  ]
}
```

`data/metadata/items.json`:

```json
{
  "items": [
    {
      "itemId": "item_1",
      "angle": 12.5,
      "efficiency": 0.91
    },
    {
      "itemId": "item_2",
      "angle": 14.0,
      "efficiency": 0.93
    }
  ]
}
```

Run the connector with:

```bash
dbslice-ai-connector run \
  --dataset "example=Example study=/absolute/path/to/example-dataset"
```

Here, `example` is the name used for this dataset within the connector. It may
contain letters, numbers, dots, underscores and hyphens, and must start with a
letter or number.

## Configuration

Every dataset must have `config/config.json` containing these three sections:

| Section | Purpose |
|---|---|
| `dataset` | Describes the dataset as a whole |
| `metaData` | Locates and describes the item list |
| `extracts` | Describes files associated with each item |

`dataset` and `metaData.config` may contain any JSON fields useful for
describing the study. `metaData.path` gives the location of the item list,
relative to the dataset directory.

Each extract has:

- `extractId`: its name within the dataset
- `type`: `image`, `line` or `glb`
- `description`: a short explanation
- `path`: the file location, relative to the dataset directory

`format`, `xLabel` and `yLabel` may also be supplied where useful.

Use `${itemId}` in an extract path when each item has its own file. No other
path placeholders are supported.

Store each extract under `data/extracts/<extractId>/`. This keeps all files for
one extract together and makes the directory easy to inspect. For example:

```text
data/extracts/
├── pressure/
│   ├── item_1.png
│   └── item_2.png
└── velocity-profile/
    ├── item_1.json
    └── item_2.json
```

## Items

The file named by `metaData.path` must contain an `items` array. Every item must
have a non-empty string `itemId`. All other fields are chosen by the dataset
author and may contain strings, numbers, booleans, arrays, objects or `null`.

Properties intended for filtering, plotting or analysis should normally be
stored directly on each item, as in `angle` and `efficiency` above.

## Images

An image extract points to one image for each item:

```json
{
  "extractId": "pressure",
  "type": "image",
  "description": "Surface pressure",
  "format": "png",
  "path": "data/extracts/pressure/${itemId}.png"
}
```

Supported image formats are PNG, JPEG, GIF and SVG.

## Line data

A line extract points to a JSON file:

```json
{
  "extractId": "velocity-profile",
  "type": "line",
  "description": "Velocity through the passage",
  "format": "json",
  "xLabel": "Distance",
  "yLabel": "Velocity",
  "path": "data/extracts/velocity-profile/${itemId}.json"
}
```

The line file can be an array of points:

```json
[
  { "x": 0.0, "y": 12.1 },
  { "x": 0.5, "y": 14.8 },
  { "x": 1.0, "y": 13.2 }
]
```

It can alternatively contain a label and a `data` array:

```json
{
  "label": "item_1",
  "data": [
    { "x": 0.0, "y": 12.1 },
    { "x": 0.5, "y": 14.8 }
  ]
}
```

Every point must contain finite numeric `x` and `y` values.

## 3D models

A `glb` extract points to a binary glTF file:

```json
{
  "extractId": "geometry",
  "type": "glb",
  "description": "Three-dimensional geometry",
  "format": "glb",
  "path": "data/extracts/geometry/${itemId}.glb"
}
```

## Embeddings

An extract may have an associated grid or cell embedding. For a stored
embedding, add an `embedding` section to the extract:

```json
{
  "extractId": "pressure",
  "type": "image",
  "description": "Surface pressure",
  "format": "png",
  "path": "data/extracts/pressure/${itemId}.png",
  "embedding": {
    "type": "grid",
    "source": "file",
    "description": "A two by two pressure summary",
    "path": "data/extracts/pressure/${itemId}_embedding.json",
    "settings": {
      "shape": [2, 2]
    }
  }
}
```

The embedding file contains its shape and cells:

```json
{
  "shape": [2, 2],
  "cells": [
    { "index": [0, 0], "avg": 0.12 },
    { "index": [0, 1], "avg": 0.18 },
    { "index": [1, 0], "avg": 0.15 },
    { "index": [1, 1], "avg": 0.21 }
  ]
}
```

Each `index` contains one or more zero-based integers. Each `avg` must be a
finite number. A cell may also have a `label` and other JSON properties.

Some embeddings can instead be computed by dbsliceAI. Such configurations use
`"source": "computed"` and name a supported `method`; they do not have an
embedding file or `embedding.path`.

## File and size rules

- Paths should be relative to the dataset directory.
- A path that resolves outside that directory is rejected, including a
  symbolic link that points outside it.
- Referenced files are read only when requested.
- An unresolved `${...}` placeholder is rejected.
- An individual image, GLB, line or embedding payload is limited to 16 MiB.
- Dataset paths are kept private and are not returned to dbsliceAI clients.
