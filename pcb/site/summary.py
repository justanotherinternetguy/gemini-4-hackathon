"""Build the 60-second summary video: runs/site/video/summary.mp4 (1920x1080, H.264, no audio).

    .venv/bin/python -m pcb.site.summary

Order: title card, scoreboard, the two featured board videos (sped up), "what the agents decided",
result card. Stills are headless-Chromium screenshots of the site (pcb/site/shots.sh); the board
videos come from pcb.site.videos.
"""
from __future__ import annotations

import json
import subprocess

from pcb.site.data import ROOT

SITE = ROOT / "runs" / "site"
STILL = {"title": 4.5, "score": 7.5, "decisions": 9.5, "result": 8.0}
BOARD_S = 14.0     # each board video is sped up (or slowed) to this length


def dur(p) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def main() -> None:
    subprocess.run([str(ROOT / "pcb" / "site" / "shots.sh")], check=True, stdout=subprocess.DEVNULL)
    shots = SITE / "shots"
    pick = json.loads((SITE / "pick.json").read_text())
    vids = [SITE / "video" / f"{x['board']}.mp4" for x in pick["featured"]]
    vids = [v for v in vids if v.is_file()]
    parts = [("still", shots / "card1.png", STILL["title"]), ("still", shots / "s1.png", STILL["score"])]
    parts += [("video", v, BOARD_S) for v in vids]
    parts += [("still", shots / "card2.png", STILL["decisions"]), ("still", shots / "card3.png", STILL["result"])]
    cmd, filt = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, (kind, path, d) in enumerate(parts):
        if kind == "still":
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{d}", "-i", str(path)]
            filt.append(f"[{i}:v]scale=1920:1080,setsar=1,fade=t=in:st=0:d=0.35,fade=t=out:st={d - 0.35:.2f}:d=0.35,"
                        f"format=yuv420p[v{i}]")
        else:
            cmd += ["-i", str(path)]
            k = d / dur(path)
            filt.append(f"[{i}:v]setpts={k:.5f}*PTS,fps=30,scale=1920:1080,setsar=1,trim=duration={d},"
                        f"fade=t=in:st=0:d=0.3,format=yuv420p[v{i}]")
    filt.append("".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[out]")
    out = SITE / "video" / "summary.mp4"
    cmd += ["-filter_complex", ";".join(filt), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "20", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print(f"wrote {out}: {dur(out):.1f} s ({len(vids)} board videos)")


if __name__ == "__main__":
    main()
