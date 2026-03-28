# CSI Tool

Native Canon CR3 burst extractor with both a desktop GUI and a CLI.

CSI Tool opens Canon raw burst or roll `.CR3` files, parses the ISOBMFF container directly, and rebuilds each frame as an individual raw `.CR3` file. The current version does this with a built-in Python backend, so it no longer depends on `dnglab` for the main extraction workflow.

## Features

- Extracts individual raw `.CR3` files from Canon burst `.CR3` containers
- Includes a Tkinter desktop app for browsing and batch extraction
- Includes a CLI for automation and scripting
- Parses burst metadata without loading the full file into memory at once
- Rebuilds per-frame CR3 containers with updated sample tables and Canon metadata
- Preserves a clean frame naming scheme like `FILE_frame_0001.cr3`

## Why This Exists

Canon burst files are not simple folders of embedded raws. They are ISO Base Media File Format containers that hold multiple image tracks and metadata tables inside a single `.CR3` file. Many tools either do not support these files well or convert them to another format first.

CSI Tool is focused on a more direct workflow:

- detect burst files
- inspect frame metadata
- reconstruct each frame as a standalone raw CR3

## Project Structure

- `csi_tool/core/cr3_parser.py`
  Parses burst structure and reports frame count, offsets, and image metadata.
- `csi_tool/core/native_cr3_backend.py`
  Native extractor that rebuilds single-image CR3 files from burst containers.
- `csi_tool/core/extractor.py`
  Orchestrates threaded extraction for the GUI and synchronous use in the CLI.
- `csi_tool/gui/`
  Desktop interface built with Tkinter.
- `csi_tool/cli/cli.py`
  Command-line entry points for file inspection and extraction.

## Requirements

- Windows with Python 3.11+
- No third-party Python package is required for the core app
- Tkinter must be available in the Python installation

`requirements.txt` is intentionally minimal because the active extraction path uses only the Python standard library.

## Running The App

Launch the GUI:

```powershell
py -m csi_tool
```

Show burst file info:

```powershell
py -m csi_tool info "C:\path\to\burst.CR3"
```

Extract every frame as raw CR3:

```powershell
py -m csi_tool extract "C:\path\to\burst.CR3"
```

Extract selected frames:

```powershell
py -m csi_tool extract "C:\path\to\burst.CR3" --frames "1,3,5-10"
```

Batch extract a folder:

```powershell
py -m csi_tool batch "C:\path\to\folder"
```

## How It Works

At a high level the native backend:

1. Opens the burst CR3 file as an ISOBMFF container.
2. Reads top-level boxes like `ftyp`, `moov`, `mdat`, and Canon UUID boxes.
3. Walks track metadata to find per-frame sample sizes and offsets.
4. Extracts the sample data for one frame across the relevant tracks.
5. Rebuilds a single-image `moov` box with one-sample `stsz`, `stsc`, `stts`, and `co64` tables.
6. Writes a new CR3 file containing the rebuilt metadata plus the frame payload.

This is designed to keep the output in raw CR3 form instead of converting to DNG.

## Current Status

The current repository includes:

- native raw CR3 extraction backend
- GUI workflow for adding files, selecting output folders, and batch extraction
- CLI workflow for info, single extraction, and directory batch extraction
- compile and import smoke checks on the refactored codebase

## Notes And Limitations

- Canon burst variations differ by camera model and firmware, so some files may need additional tuning.
- The extractor currently targets the burst container structure and metadata patterns observed in public reverse-engineering references and community examples.
- Preview and some Canon-private metadata may differ slightly from Canon DPP-generated exports, but the main goal is usable standalone raw CR3 outputs.

## References

This project was informed by public reverse-engineering and community work around Canon burst CR3 support, especially:

- [AGFeldman/canon_burst_image_extract](https://github.com/AGFeldman/canon_burst_image_extract)
- [lclevy/canon_cr3](https://github.com/lclevy/canon_cr3)
- [Canon R6 II RAW Burst discussion on pixls.us](https://discuss.pixls.us/t/canon-r6-ii-raw-burst/45717)

## Development

Compile the package to catch syntax issues:

```powershell
& "C:\Users\sunny\AppData\Local\Programs\Python\Python311\python.exe" -m compileall csi_tool
```

## License

No license file is currently included. Add one before distributing the project more broadly.
