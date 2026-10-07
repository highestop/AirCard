"""Locate checkout or app-bundle resources independently of the working directory."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = (ROOT.parent / "Helpers" if ROOT.name == "Resources" and ROOT.parent.name == "Contents"
         else ROOT / "build")
