"""Wall, door, window, stair and fallback floor tile names for buildings (all verified against vanilla lot
data / the game's tile definitions). Ground and street tiles live in terrain.py."""
from dataclasses import dataclass

@dataclass
class WallStyle:
    W: str
    N: str
    NW: str
    SE: str
    winW: str      # wall piece with window opening (W / N)
    winN: str
    doorW: str     # wall piece with door opening
    doorN: str


def _sheet(prefix, base, n=16, win=(8, 9), door=(10, 11)):
    f = lambda i: f'{prefix}_{base + i}'
    return WallStyle(f(0), f(1), f(2), f(3), f(win[0]), f(win[1]), f(door[0]), f(door[1]))


# exterior wall families (walls_exterior_house_01: 0=grey brick, 4=red brick; frames at +8/+10 within each half-sheet)
BRICK_RED = WallStyle('walls_exterior_house_01_4', 'walls_exterior_house_01_5', 'walls_exterior_house_01_6',
                      'walls_exterior_house_01_7', 'walls_exterior_house_01_12', 'walls_exterior_house_01_13',
                      'walls_exterior_house_01_14', 'walls_exterior_house_01_15')
BRICK_GREY = WallStyle('walls_exterior_house_01_0', 'walls_exterior_house_01_1', 'walls_exterior_house_01_2',
                       'walls_exterior_house_01_3', 'walls_exterior_house_01_8', 'walls_exterior_house_01_9',
                       'walls_exterior_house_01_10', 'walls_exterior_house_01_11')
INTERIOR = WallStyle('walls_interior_house_01_0', 'walls_interior_house_01_1', 'walls_interior_house_01_2',
                     'walls_interior_house_01_3', 'walls_interior_house_01_8', 'walls_interior_house_01_9',
                     'walls_interior_house_01_10', 'walls_interior_house_01_11')
# storefront: glass walls with a door frame (walls_commercial_01)
STOREFRONT = WallStyle('walls_commercial_01_16', 'walls_commercial_01_17', 'walls_commercial_01_18',
                       'walls_commercial_01_19', 'walls_commercial_01_16', 'walls_commercial_01_17',
                       'walls_commercial_01_8', 'walls_commercial_01_9')

# door / window fixtures placed on top of frame pieces
DOOR_W, DOOR_N = 'fixtures_doors_01_0', 'fixtures_doors_01_1'
WINDOW_W, WINDOW_N = 'fixtures_windows_01_0', 'fixtures_windows_01_1'

# low parapet walls for flat roofs (walls_exterior_house_low_01: red brick 4..7, grey 0..3)
PARAPET_RED = ('walls_exterior_house_low_01_4', 'walls_exterior_house_low_01_5', 'walls_exterior_house_low_01_6', 'walls_exterior_house_low_01_7')
PARAPET_GREY = ('walls_exterior_house_low_01_0', 'walls_exterior_house_low_01_1', 'walls_exterior_house_low_01_2', 'walls_exterior_house_low_01_3')
ROOF_FLOOR = ['floors_exterior_tilesandstone_01_%d' % i for i in (48, 49)] if False else ['floors_exterior_street_01_16']

# stairs: (bottom, middle, top) tile names. W = ascending towards west (top at low x); N = ascending north (top at low y)
STAIRS_W = ('fixtures_stairs_01_16', 'fixtures_stairs_01_17', 'fixtures_stairs_01_18')
STAIRS_N = ('fixtures_stairs_01_24', 'fixtures_stairs_01_25', 'fixtures_stairs_01_26')

# interior floors (indices verified by rendering the sheets)
FLOOR_WOOD = [f'floors_interior_tilesandwood_01_{i}' for i in (42, 43, 44, 49, 50, 51)]
FLOOR_DARKWOOD = [f'floors_interior_tilesandwood_01_{i}' for i in (40, 41, 45, 46, 47)]
FLOOR_TILE_WHITE = [f'floors_interior_tilesandwood_01_{i}' for i in (25, 26, 27, 54, 55)]
FLOOR_CONCRETE = [f'floors_interior_tilesandwood_01_{i}' for i in (24, 28, 29)]
FLOOR_LINO = [f'floors_interior_tilesandwood_01_{i}' for i in (36, 37)]
FLOOR_CARPET = [f'floors_interior_carpet_01_{i}' for i in (0, 2, 3, 5, 7, 8, 11, 12, 32)]
