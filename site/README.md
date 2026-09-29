# Launch site and video

A static landing page and a rendered launch video for workflow-sim. Neither is
part of the Python package; `site/` is excluded from the wheel and sdist.

| Path | What it is |
| --- | --- |
| `index.html` | The whole site. Inline CSS, system fonts, no JavaScript, no external requests. |
| `media/launch-1920x1080.mp4` | Main video, 44 s, H.264 High, 30 fps, no audio. |
| `media/launch-1080x1080.mp4` | Square cut for social posts. |
| `media/launch.en.vtt` | Captions and transcript. |
| `media/poster.jpg` | Video poster (the failing check, at 23.5 s). |
| `video/render.py` | Renderer. Pillow draws every frame; ffmpeg encodes. |
| `video/render.py.lock` | uv lock for the renderer's only dependency, Pillow. |

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
uv run --locked --script site/video/render.py --still 23.5 --out /tmp/frames
```

A full render takes about 20 seconds on an Apple silicon laptop. Captions, their
timing and the scene schedule are constants at the top of `render.py`; the VTT
file is written from the same list, so edit the text there, not in the VTT.
The transcript in `index.html` is maintained by hand: update it when you change
a caption.

## What the video shows

Everything comes from `src/workflow_sim/examples/billing.py` and its two runs:

```sh
uv run workflow-sim workflow_sim.examples.billing:build --duration 12                                # PASS
uv run workflow-sim workflow_sim.examples.billing:build --duration 12 --inputs broken.json         # ASSERTION_FAILED
```

The bug and fix lines are the two branches of
`key = f"{invoice['id']}:{attempt}" if broken else invoice['id']`. If that example
changes, check the keys, values and timings in `render.py` and `index.html`.

## Before publishing

- The call to action links to the owner's GitHub profile because there is no
  access route yet. Replace it in `index.html` and in `render.py` (`end_card`,
  `END_TRANSCRIPT`) when one exists, then re-render.
- The repository is private. Don't add links to repository docs from the page;
  visitors without access would get a 404.
