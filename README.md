# osm2zomboid

**Turn any real place into a playable Project Zomboid (Build 42) map, built the way the game's own map is built.**

osm2zomboid is a **free and open-source** project, released under the [MIT License](LICENSE): anyone may use it,
study it, change it and share it, including their changes. Contributions are welcome (see
[Contributing](#contributing)).

It reads a real area from [OpenStreetMap](https://www.openstreetmap.org) (streets, buildings, land use, shops,
parks, car parks) and writes a ready-to-install map mod: terrain, roads with curbs and lane lines, sidewalks,
car parks with cars, lawns and driveways, and every building fully built: walls, shop fronts, doors, windows,
stairs, lifts, roofs, and rooms furnished like Knox County's. It writes the game's map files directly; no
TileZed or WorldEd is needed.

## Goals
* **One shot.** Point it at your town, your street or a big piece of your city, and get a map you can play
  straight away, without editing by hand.
* **Faithful to the real place.** Streets, blocks and buildings stand where they really are and at their real
  size (one square is one metre). A corner store mapped on a corner is a corner store on that corner.
* **Faithful to the game.** Everything looks and plays like vanilla Knox County, not like a generated map. Every
  rule (what goes in a room, how a shop is stocked, how full a room is, where furniture stands, how grimy a wall
  is, how a car park is laid out) is **measured from the vanilla map**, not guessed, and checked against it.
* **Anyone can use it.** No mapping tools to learn; a command (and later a simple window) and a place name.

## Status
Working and playable, under active development and playtesting. The included preset is downtown
**Austin, TX** around 6th Street (about 1 km x 0.75 km, 330+ buildings, towers up to 28 floors). Residential
neighbourhoods (houses, rows of townhomes, yards, driveways) are being tuned now. Known limits are listed at the
end. Please report anything that looks wrong in game, ideally with a screenshot and a rough location.

## Requirements
* Project Zomboid **Build 42**, installed (the tool reads your own copy of the game; see below)
* Python 3.10+ and `pip install -r requirements.txt` (pillow, numpy, scipy)
* An internet connection the first time you build a new area (OpenStreetMap's Overpass API)

## No game files are included
This repository contains **no Project Zomboid files**. On first run the tool reads the tile definitions and the
vanilla map **from your own installed copy of the game** and stores its measurements in `cache/` on your PC,
which is never published (see `.gitignore`). Generated maps go to `output/` and to your `Zomboid/mods` folder.

## Where the Austin map sits
South-east of Muldraugh. Drive south out of town on the highway to the cross junction (world x ~10560,
y ~11204, cell 41,43) and turn east. That road (15 squares wide, world rows 11197-11211) used to stop in the
forest at x = 12899; the mod continues it 156 squares to the west edge of Austin, where it becomes 6th Street.

* Map block: cells **51-54 x 42-44** (world x 13056-14079, y 10752-11519). All twelve were forest in vanilla.
* Connector: vanilla cell **50,43** is imported whole and only the strip of road is repainted. It is a copy of the
  vanilla road's own last squares, so the join cannot be seen. The cell keeps its vanilla `chunkdata` file.
* The map is turned ~18 degrees so that Austin's street grid runs along the tile axes (straight walls and streets
  instead of staircases). The top of the map therefore points about 18 degrees east of north.

## Quick start
1. Install Python 3.10+ and run once:  `pip install -r requirements.txt`
2. Double-click **run_austin.bat** (builds the mod and copies it to `%USERPROFILE%\Zomboid\mods\Austin6thStreet`).
   Takes about three minutes. The OpenStreetMap data for Austin is cached in `cache/`, so it works offline.
3. Start Project Zomboid -> Mods -> enable **Austin, TX - 6th Street** -> New Game. On the spawn screen pick
   **Austin, TX - 6th Street**; you start on a sidewalk downtown (world 13568, 11108 or one of four other spots).
4. Optional: **preview_austin.bat** renders `preview.png` (an isometric cutaway drawn with the game's own sprites;
   trees are drawn by the game from other sprites and do not show in previews).

## Make your own map
```
python survey.py 60 10 72 18 site.png
python generate.py --name MyTown --title "My Town" --lat 40.7484 --lon -73.9857 --cells 4x3 --cell0 66,13 --entry-y 0 --install
```
* `survey.py CX0 CY0 CX1 CY1 out.png` draws a block of vanilla cells with the cell grid and lists what each one
  holds. A map mod replaces every vanilla cell it covers, so pick cells marked `forest`. `generate.py` warns when
  the block you chose holds rooms, roads or rails.
* `--lat/--lon`  centre of the area. `--cells WxH` size in 256x256 cells (1 tile ~ 1 metre; 4x3 is ~1 km x 0.75 km).
* `--cell0 X,Y`  world cell of the map's north-west corner.
* `--rotate`     `auto` (default) turns the map so the street grid follows the tile axes when the area has a clear
  grid; `0` keeps north up; any other number forces that angle.
* `--background` what lies between mapped features: `auto` (paved lots downtown, lawns in suburbs, grass beyond),
  `grass` or `concrete`.
* `--zombies`    zombie density. `0.5` (default) is half the spread of downtown Louisville, `1` the full spread, `0` none.
* `--entry-y 0`  no connector. To join a vanilla road instead, give all four:
  * `--entry-y`      world y of the centre line of the vanilla road (Austin: 11204.5 for rows 11197-11211)
  * `--vanilla-end`  world x of the first square past the end of that road (Austin: 12900)
  * `--link-cell`    the vanilla cell that holds the road's end (Austin: 50,43); the connector runs east from it
    through every cell up to the map, importing each one and keeping its `chunkdata`
  * `--entry-road`   name of the street in your area that should meet it (Austin: `6th Street`); the map is shifted
    so that street arrives exactly on the connector
  * `--entry-width`  optional; 0 copies the vanilla road as it is, a number paves a plain road that wide
* `--install`    copies the mod into `Zomboid/mods`. `--pz-dir` if the game isn't found automatically.
* Needs internet the first time for a new area (Overpass API; results are cached in `cache/`).

## How it works (files)
| file | job |
|---|---|
| `osmfetch.py`, `osmmodel.py` | download OpenStreetMap features; project them to tile coordinates, rotated to the street grid |
| `ground.py` | read the outdoor tags: terrain class per square (from `surface`, `landuse`, `leisure`, `natural`...), road widths from lanes / width / parking tags, sidewalks, car parks, vegetation zones, crossings, fences, street objects, entrances |
| `terrain.py` | ground tiles the vanilla way: surface families, edge blends, curbs, grass tufts, lane lines, crosswalks, parking stalls, trees, lamps, signals, benches, fences, zombie density |
| `buildings.py` | footprint -> walls / doors / windows / storefronts, room layout, room programme from the tags, stairs, roofs |
| `vanillastats.py` | measures how the vanilla map furnishes every room type -> `cache/vanilla_rooms.json` |
| `vanillawalls.py` | measures vanilla wall, door and window choices per room type and building -> `cache/vanilla_walls.json` |
| `roofs.py` | cuts the equipment groups (air conditioners, ducts, vents) off vanilla flat roofs -> `cache/vanilla_roofs.json`, and sets them on generated roofs |
| `plan.py` | floor plan of any part of a map, vanilla or generated: rooms by name, walls, doors, windows, furniture |
| `audit.py` | sanity checks of every room of a map against vanilla's own rates |
| `pitched.py` | copies pitched roofs off vanilla houses -> `cache/vanilla_pitched.json` and fits them to generated houses |
| `interiors.py` | copies whole vanilla rooms (11,000 of them, 518 kinds) -> `cache/vanilla_interiors.json` and fits them to generated rooms |
| `zones.py` | `objects.lua`: parking stalls (cars), town, zombie-type, foraging and road zones |
| `worldmapgen.py` | the in-game map: roads, buildings, water, street names, town label, painted terrain |
| `weathering.py` | grime on walls, floors, sidewalks and roads at vanilla's rates (downtown vs small town) |
| `worldmapbin.py` | reads and writes `worldmap.xml.bin`, the world map file the game actually loads |
| `poster.py` | the Mods-screen picture: a street-level slice of the town's busiest shopping street (`poster.png`, `icon.png`) |
| `furnish.py` | furnishes rooms from those measurements |
| `tilepalette.py` | wall, door, window and stair tile names |
| `catalog.py` | reads the game's tile definitions (furniture facings, multi-tile pieces) |
| `lotwriter.py`, `lotreader.py` | the B42 map file format, including basement levels |
| `chunkdata.py` | reads and writes `chunkdata_X_Y.bin`, the collision map the game's off-screen zombie simulation uses |
| `vanilla_import.py` | loads a vanilla cell so the connector can repaint part of it |
| `survey.py` | overview of vanilla cells for choosing a site |
| `roundtrip.py` | proves reader + writer against vanilla cells (tiles, rooms, buildings, zombie map come back identical) |
| `sprites.py`, `preview.py` | texture-pack reader + isometric preview renderer |

## Ground tiles
Nothing about tile choice is guessed; the rules in `terrain.py` were measured on the vanilla cells.
* Each surface (asphalt, concrete, gravel, dirt, sand, lawn, wild grass, water) is a family of full tiles plus edge
  pieces. Where two surfaces meet, the higher one bleeds onto the lower with the matching half or strip piece.
* Streets get curbs against sidewalks, and against grass only in built-up areas; country roads fade into the grass.
* Two-way streets get the double yellow centre line, multi-lane streets dashed white dividers, wide ones edge
  lines. Mapped crossings become ladder crosswalks.
* **Car parks** are laid out like vanilla's: 3-wide, 5-deep stalls in back-to-back rows along the lot's long side,
  each row facing a 6-square drive aisle, a white line between stalls and across their backs. Every stall is a
  `ParkingStall` zone, so the game parks cars (and wrecks) in them. Multi-storey garages become surface lots.
* Mapped trees, street lamps, traffic signals, stop signs, benches, bus stops, bins, post boxes, fences, walls and
  hedges are placed where OpenStreetMap has them. Lamps, hydrants, drains, parking meters (on streets tagged with
  parking) and street trees fill in where nothing is mapped.

## Zombies
* Density is one number per 8x8 chunk in each cell's `.lotheader`. Vanilla uses 0-10; downtown Louisville
  averages 2.2 and its densest cell 2.7.
* Every chunk gets a heat score the way vanilla's own map behaves: tall buildings most, then the kind of rooms
  (plus a bonus for bars), with parks, water and building sites lowest and the edge of the map fading out. Scores
  are blended over neighbourhoods. Chunks are then ranked and given intensities in the same proportions as
  downtown Louisville (13% at 0, 13% at 1, 43% at 2, 12% at 3, the rest at 4, nothing higher). No cell may average
  more than vanilla's densest cell. At the default `--zombies 0.5` the Austin preset averages 1.05.
* Each cell also gets a `chunkdata` file: per square, whether it is solid, has a wall on its north or west edge,
  is water, or is inside a room. The game uses it for zombies in parts of the map that are not loaded, so they
  walk around buildings instead of through them. The format comes from the game's own map converter; the writer
  reproduces all 4,065 vanilla files byte for byte, and its rules match vanilla on 99.8% of squares or better.

## Buildings and furniture
The rules below were read off the vanilla map (`plan.py` and `preview.py` draw any vanilla block for comparison),
and `audit.py` checks a generated map against them (see "Checking a map").
* **What a building is.** Its own tags first. A business mapped in or just in front of a building takes the
  street unit at that spot (or the nearest free one), so a corner store mapped on a corner is a `cornerstore`
  on that corner. Over 90% of Austin's mapped businesses end up where they are mapped. Kinds without a vanilla
  twin get the closest one: offices of any kind are `office`, venues are bars or theatres, fuel stations are a
  `gasstore` with Gas 2 Go or Fossoil pump islands on the forecourt.
* **Untagged buildings** (most of OpenStreetMap outside downtowns is just `building=yes`) are read from where
  they stand: mapped land use and how built up the neighbourhood is. In a residential street a building is a
  house, a small one in a yard a shed or garage; on industrial land a warehouse; downtown a row of shops with
  offices or flats above. A big building among houses is a **row of homes**: it is cut into house-wide units
  (vanilla houses are about 8 x 12 squares) that share blank walls, one look and one roof, like a terrace.
* **Roads and sidewalks win**: where a mapped outline reaches over a road or sidewalk, the building is trimmed
  back (vanilla never builds on a sidewalk).
* **Houses** are squared to the box shape vanilla houses have, get a vanilla pitched roof (copied from a vanilla
  house of the same width and lengthened along the ridge, kept only if it closes over the whole house when drawn),
  a living room you enter with the kitchen open to it, a dining room, laundry and bathroom, bedrooms upstairs off
  a landing, and a yard: a mown lawn (two shades of grass and short tufts, as vanilla's yards), chain-link fence
  round the back, bushes and flowers in front, an air conditioner on the wall, bins and a barbecue out back. About
  80% of houses get a driveway from the street with a car spot at the house end (vanilla: 81%). Bigger or L-shaped
  houses get several vanilla roofs (parallel roofs, a roof per wing), never a flat one.
* **Ground floor of other buildings.** The rooms on the street are units: a shop, a dining room, a bar, a lobby.
  Each has its own street door and shop windows, and its back rooms behind it: kitchen, stock room, toilet.
  Kitchens and stock rooms often have a back door with a lamp, and dumpsters stand by most of them. Offices, flats
  and hotels are entered through a small lobby with the stairs. A big building keeps its units along the street
  and has a service core behind them: a corridor with stores, offices, toilets and a janitor's closet. Nobody
  sleeps on the ground floor of a commercial building.
* **Upper floors.** Corridors with rooms off them, like vanilla: open-plan offices with a service block, hotel
  rooms with their own bathroom, flats where the kitchen is open to the living room and the bedrooms and bathroom
  open off it. Rooms are kept large; vanilla's logic is a few big rooms, not hundreds of cells.
* **Walls.** A wall tile shows the side it stands on, so the north and west walls of every room are painted for
  that room (vanilla does this on 100% of its walls). Each room type gets one of the wall families vanilla uses
  for it, each building an outer material and roof parapet that go together in vanilla, each room type its kind
  of door. Windows take the share of a wall that vanilla gives that room type (offices about 1 in 5, store rooms
  almost none). Walls against a neighbouring building are blank.
* **Roofs.** Flat roofs with vanilla's asphalt floor and parapet, and equipment cut whole from vanilla roofs
  (air conditioners with their ducts, vents, tanks) at vanilla's density.
* **Towers** take their real height up to vanilla's tallest, 28 floors; a taller one keeps its plan. About 40% of
  the office floors of a tower are left `empty`, as in vanilla's towers (also lighter to load). A tower's lobby is
  big enough for its stairs and lifts. Like vanilla, every building of 10 floors or more has a lift, and those of
  5 to 9 floors where one fits (vanilla: 42% of them): a 4 x 3 concrete shaft through every floor, its two-door
  pair and call button on the corridor or a lift lobby off it, and on the lobby or a corridor to it on the ground
  floor. They are vanilla's own sprites, so
  [Gun's Elevator](https://steamcommunity.com/sharedfiles/filedetails/?id=3411580812) runs them; without that mod
  they are closed doors. The stairs go on up into a small room on the roof with a door out onto it.
* **Shops** are furnished as a whole vanilla shop of the kind, and a shop bigger than that keeps lining its walls
  with the shelving, racks and freezers vanilla's shops of that kind use, until as much wall is stocked as in
  vanilla (corner stores about half their wall, clothes stores a fifth, jewellers almost none). Nothing tall
  stands in front of a window.
* **Furniture** is not a hand-made list. `vanillastats.py` reads about 80,000 vanilla rooms and records, per room
  type, which pieces appear, how often, how many per square and in which styles, and per piece how it stands.
  `furnish.py` rolls a room from those numbers. A room much bigger than vanilla's is treated as a few vanilla
  rooms, not scaled up piece by piece, and big stock rooms are furnished like vanilla's big stock rooms. Toilets
  in shops and offices have no bathtub. Every room gets a light switch, so every room, hallways included, has
  light; halls and lobbies also get vanilla's wall lamps.

## Checking a map
For a quick look at rule changes without the full build (most of the time goes into furnishing every room),
build only the buildings in a box of world squares; it goes to `output/<name>_test` and is never installed:
```
python generate.py --only 13300 11150 13360 11210
python preview.py output/Austin6thStreet_test/common/media/maps/Austin6thStreet 13300 11150 13360 11210 block.png 0
```
```
python audit.py output/MyTown/common/media/maps/MyTown
```
lists, per 100 rooms, the rooms no door leads into, rooms you cannot reach from the street, doors to nowhere,
doors blocked by furniture, windows between rooms, rooms without what nearly every vanilla room of their kind has
(a toilet, a bed, a stove), rooms with nothing to loot, oversized rooms, bedrooms on commercial ground floors and
holes in floors; and how the furniture stands: tills turned away from the way in, toilets facing the door, beds
with no way in, tall pieces in front of windows (as a share of all tills, toilets, beds and windows). Run on vanilla blocks it gives vanilla's own rates (Louisville: 0.7 dead rooms, 2 missing
essentials per 100); Austin, a Hyde Park suburb, Bastrop's main street and East Austin all come out at or below
those.

## Status / known limits
* Verified: file formats (round-trip, `python roundtrip.py`; chunkdata against every vanilla file; the world map
  binary against vanilla's), every tile and room name against the game's definitions, and the maps in game.
* The in-game map and Spawn Selector read `worldmap.xml.bin`: Build 42's XML world-map reader drops every shape,
  so the tool writes the binary form (`worldmapbin.py`), with no more detail per cell than vanilla's busiest.
* Mod layout: the map lives in `common/media/maps/<name>/`. A map placed under `42/media/maps/` loads as a mod
  but never joins the world (no cells, no spawn entry); earlier builds of this tool did that.
* Towers cap at 28 floors (`--max-levels`). Pitched roofs only on houses.
* Maps made at the same spot (the default one, beside Muldraugh) overlap; the tool marks them incompatible with
  each other in their `mod.info`, so only one can be enabled.

## Contributing
Issues and pull requests are welcome: bug reports with a screenshot and location, rules measured from the vanilla
map, new presets. Please keep the project's rule of thumb: a generation rule should be measured from the vanilla
map (and `audit.py` should not get worse), not invented. Never commit game files or anything from `cache/`.

## Credits and disclaimers
* **License:** the code is MIT-licensed (see [LICENSE](LICENSE)).
* **Map data** (c) [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
  [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). If you publish a map made with this
  tool, credit "Map data (c) OpenStreetMap contributors" (generated mods include an `ATTRIBUTION.txt`).
* **Not affiliated with The Indie Stone.** This is an unofficial, non-commercial fan project. *Project Zomboid* is
  a trademark of The Indie Stone. No game files are included or distributed; the tool works from your own copy of
  the game. Please do not contact The Indie Stone about problems with this tool or its maps.
* Generated maps are fiction placed on real streets: everything inside the buildings (rooms, furniture, loot,
  zombies) is invented and says nothing about the real places or the people there.
* Optional mods the maps work with: [Gun's Elevator](https://steamcommunity.com/sharedfiles/filedetails/?id=3411580812)
  (lifts) and Spawn Selector (choosing a start point on the map). They are separate works by their own authors.
