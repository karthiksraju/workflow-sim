# Launch site and video

A static landing page and a rendered launch video for workflow-sim. Neither is
part of the Python package; `site/` is excluded from the wheel and sdist.

| Path | What it is |
| --- | --- |
| `index.html` | The whole site. Inline CSS, system fonts, no JavaScript, no external requests. |
| `media/launch-1920x1080.mp4` | Main video, 64 s, H.264 High, 30 fps, no audio. |
| `media/launch-1080x1080.mp4` | Square cut for social posts. |
| `media/launch.en.vtt` | Captions and transcript. |
| `media/poster.jpg` | Video poster (the failing check, at 40 s). |
| `video/render.py` | Renderer. Pillow draws every frame; ffmpeg encodes. |
| `video/render.py.lock` | uv lock for the renderer's only dependency, Pillow. |
| `alternatives/` | Three candidate landing pages and a chooser. See its README. |

## Preview

```sh
cd site && python3 -m http.server 8000
```

Open <http://localhost:8000>. Any static host works; serve `site/` as the root.

## Rebuild the video

Needs macOS (it uses the system San Francisco fonts), uv and ffmpeg with libx264.

```sh
uv run --locked --script site/video/render.py                    # both MP4s, captions, poster
uv run --locked --script site/video/render.py --layout wide      # one format
uv run --locked --script site/video/render.py --still 40 --out /tmp/frames
```

Captions and their timing live in `render.py`. The renderer writes
the VTT from the same list. The transcript in `index.html` is maintained by hand: update it when you change
a caption.

## What the video shows

The film shows the consumer workflow: model the service, schedule events with
`ctx.at`, register an exact `ctx.expect` assertion, run the CLI, inspect evidence,
and rerun after a code change. It is a rendered walkthrough, not a screen recording.

Source and values come from `src/workflow_sim/examples/billing.py` and its two runs:

```sh
uv run workflow-sim workflow_sim.examples.billing:build --duration 12        # PASS, exit 0
printf '{"broken":true}\n' > broken.json                                     # switches on the bug
uv run workflow-sim workflow_sim.examples.billing:build --duration 12 \
  --inputs broken.json                                                       # ASSERTION_FAILED, exit 1
```

The renderer reads verdicts and checks from `video/evidence.json`, a small excerpt
of two actual public-runner outputs. Receipt counts on screen summarize the exact
payload arrays; they are not literal CLI output. Full raw QA results stay outside
the package. Re-run the commands above when changing the example or claims.

The bug and fix lines are the two branches of
`key = f"{invoice['id']}:{attempt}" if broken else invoice['id']`. If that example
changes, check the keys, values and timings in `render.py` and `index.html`.

## Before publishing

- The call to action links to the owner's GitHub profile because there is no
  access route yet. Replace it in `index.html` and in `render.py` (the final scene and `CAPTIONS`) when one exists, then re-render.
- The repository is private. Don't add links to repository docs from the page;
  visitors without access would get a 404.
