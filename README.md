# CSI Tool

Canon burst `.CR3` extractor with three delivery paths:

- Tkinter desktop app
- CLI for scripting and batch runs
- Ubuntu web service for `csi.sunnygong.com`

The project parses Canon burst or roll containers directly, extracts each frame, and can output either:

- `Adobe DNG` as the default web/Desktop workflow
- `Canon CR3` as the alternate raw-preserving workflow

## What The Web Service Does

The Ubuntu service is designed for a small public beta:

1. The browser creates an extraction job.
2. The source `.CR3` uploads directly to Cloudflare R2.
3. A background worker on Ubuntu downloads the burst file, extracts all frames, and builds a ZIP.
4. The browser polls job status and receives a download link when the ZIP is ready.
5. Source files, ZIPs, and temp workspaces are automatically cleaned up after download or TTL expiry.

This avoids pushing 1 GB-class uploads through a normal Cloudflare proxied request path.

## Project Structure

- `csi_tool/core/`
  CR3 parsing, native frame reconstruction, and DNG conversion backend wiring.
- `csi_tool/gui/`
  Desktop Tkinter application.
- `csi_tool/cli/`
  CLI commands for file inspection and extraction.
- `csi_tool/web/`
  FastAPI app, worker loop, SQLite job store, storage backends, templates, and static assets.
- `docker-compose.yml`
  Ubuntu deployment with separate `web` and `worker` services.

## Requirements

### Desktop / CLI

- Python 3.11+
- Tkinter available in the Python install

### Ubuntu Web Service

- Python 3.11+
- `fastapi`, `uvicorn`, `jinja2`, `boto3`
- Linux `dnglab` binary available in the container
- Cloudflare R2 bucket and credentials for production uploads/results

Install Python dependencies:

```powershell
py -m pip install -r requirements.txt
```

## Desktop And CLI Usage

Launch the desktop app:

```powershell
py -m csi_tool
```

Inspect a burst file:

```powershell
py -m csi_tool info "C:\path\to\burst.CR3"
```

Extract every frame:

```powershell
py -m csi_tool extract "C:\path\to\burst.CR3"
```

Batch extract a folder:

```powershell
py -m csi_tool batch "C:\path\to\folder"
```

## Ubuntu Web Deployment

1. Copy `.env.example` to `.env`
2. Fill in the Cloudflare R2 values and a valid Linux `dnglab` download URL
3. Build and start the services:

```bash
docker compose up --build -d
```

The stack exposes:

- `web` on `127.0.0.1:${CSI_WEB_HOST_PORT}` with `6030` as the default host port
- `worker` as the single-job background processor

Recommended production shape:

- Cloudflare Tunnel or reverse proxy points `csi.sunnygong.com` to `http://localhost:6030`
- browser uploads go directly to R2 using presigned URLs
- ZIP downloads also come from storage links instead of streaming through the app container

## Local Web Development

You can run the web app with local filesystem storage for development and tests:

```bash
CSI_WEB_STORAGE_BACKEND=local CSI_WEB_REQUIRE_DNGLAB=false python -m csi_tool.web
```

For the worker:

```bash
CSI_WEB_STORAGE_BACKEND=local CSI_WEB_REQUIRE_DNGLAB=false python -m csi_tool.web.worker
```

## Verification

Compile the package:

```powershell
& "C:\Users\sunny\AppData\Local\Programs\Python\Python311\python.exe" -m compileall csi_tool
```

Test files for the web service live under `tests/`.

## Notes And Limits

- The web flow currently accepts one `.CR3` upload per job.
- Default public-beta limits are one active job per IP and three job creations per hour.
- The worker is intentionally single-concurrency in v1.
- `Adobe DNG` is the recommended output for Lightroom workflows.
- `Canon CR3` remains available as the alternate output path.

## References

- [AGFeldman/canon_burst_image_extract](https://github.com/AGFeldman/canon_burst_image_extract)
- [lclevy/canon_cr3](https://github.com/lclevy/canon_cr3)
- [Canon R6 II RAW Burst discussion on pixls.us](https://discuss.pixls.us/t/canon-r6-ii-raw-burst/45717)

## License

No license file is currently included. Add one before broad public distribution.
