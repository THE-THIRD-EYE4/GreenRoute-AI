"""TEST STAND-IN for the real maze_2b.py - do not submit this file.

Walls were traced automatically from a video frame of the official maze, so a few
may be wrong. It only exists so the controller can be exercised offline by
pacsim.py; on the real simulator the controller imports your own maze_2b.py.
"""
DELTA = ((0, 1), (-1, 0), (0, -1), (1, 0))
MAZE_ID = "video-trace"

LAYOUT = [
    '+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+',
    '|                           |       |       |               |',
    '+   +   +   +   +---+   +   +   +   +   +   +   +---+   +---+',
    '|   |               |   |       |       |   |       |       |',
    '+   +   +   +---+   +   +   +   +---+   +   +   +---+---+   +',
    '|   |   |       |   |       |       |   |               |   |',
    '+   +   +   +---+   +   +   +---+   +   +---+   +   +   +---+',
    '|       |               |           |           |   |   |   |',
    '+---+---+   +---+---+   +   +---+   +---+---+---+   +   +---+',
    '|   |               |   |               |                   |',
    '+   +   +---+   +   +   +---+---+---+   +---+   +   +---+   +',
    '|       |       |   |               |           |           |',
    '+   +---+   +---+   +   +---+---+   +   +---+---+   +---+   +',
    '|       |   |       |                   |           |       |',
    '+---+   +   +   +---+---+   +   +   +---+   +   +---+   +---+',
    '|                                           |                ',
    '+   +---+---+   +---+---+   +   +   +   +---+   +   +---+   +',
    '|           |                                   |           |',
    '+   +   +   +   +---+---+   +   +   +   +   +---+   +---+---+',
    '|   |   |       |                   |   |                   |',
    '+   +   +---+   +   +---+   +   +   +   +---+---+   +---+   +',
    '|           |           |           |           |   |       |',
    '+   +---+   +---+---+   +   +---+---+   +   +   +   +   +   +',
    '|       |           |                   |   |   |   |   |   |',
    '+---+   +   +---+   +---+---+   +---+   +   +   +   +   +   +',
    '|       |               |           |       |           |   |',
    '+   +   +---+   +---+   +   +---+   +---+   +   +---+   +   +',
    '|   |           |       |       |               |           |',
    '+   +   +   +---+   +   +---+   +   +   +---+   +   +---+   +',
    '        |           |                       |               |',
    '+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+',
]


class Maze:
    grid = 15
    spawn_row = 14
    exit_row = 7

    def __init__(self, layout):
        self.layout = layout

    @classmethod
    def official(cls):
        return cls(LAYOUT)

    def can_move(self, cell, d):
        r, c = cell
        dr, dc = DELTA[d]
        if not (0 <= r + dr < 15 and 0 <= c + dc < 15):
            return False
        L = self.layout
        if d == 0:
            return L[2 * r + 1][4 * c + 4] != "|"
        if d == 2:
            return L[2 * r + 1][4 * c] != "|"
        if d == 1:
            return L[2 * r][4 * c + 1:4 * c + 4] != "---"
        return L[2 * r + 2][4 * c + 1:4 * c + 4] != "---"
