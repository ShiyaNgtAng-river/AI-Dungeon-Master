#!/usr/bin/env python3
"""AI Dungeon Master Web 启动脚本。"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="AI DM Web Server")
    parser.add_argument("--mode", choices=["dev", "prod"], default="dev")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    print(f"🎲 AI Dungeon Master Web Server ({args.mode})")
    print(f"   API: http://{args.host}:{args.port}")
    if args.mode == "dev":
        print("   Frontend: cd frontend && npm run dev")

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=(args.mode == "dev"),
    )


if __name__ == "__main__":
    main()
