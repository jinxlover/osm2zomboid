@echo off
rem Builds the Austin 6th Street map mod and installs it into Zomboid\mods.
rem Needs Python 3.10+ with:  pip install -r requirements.txt
cd /d "%~dp0"
python generate.py --name Austin6thStreet --install
pause
