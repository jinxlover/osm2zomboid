@echo off
rem Renders a preview image (isometric, ground floor cutaway) of the middle of the generated Austin map.
cd /d "%~dp0"
python preview.py output\Austin6thStreet\common\media\maps\Austin6thStreet 13548 11088 13603 11138 preview.png 0 0.6
echo wrote preview.png
pause
