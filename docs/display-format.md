# How a platform indicator is laid out

Notes from measuring a photograph of a real dot-matrix platform indicator,
and what this display does with them.

![the board](images/platform_indicator.png)

## The reference

A National Rail platform indicator, counting pixels in
[this photograph](https://blog.balena.io/wp-content/uploads/2019/07/hu788k5bih421.jpg):

```
14:29 London St Pancras                    Exp 14:44
Calling at:od Junction, East Croydon, Purl
3rd 14:51 Moorgate                          On time
                   14:36:00
```

## What the measurements say

**Four rows, nine pixels each.** Every row on the board is the same height.
Nothing is bigger than anything else; the rows do not vary.

**Text uses seven of the nine.** Capitals and digits occupy the top seven
pixel rows. The remaining two are not padding: they are where descenders go,
so `g`, `y` and `p` drop below the baseline to the bottom of the row.

**The clock uses all nine.** This is the only thing on the board that does.
Digits have no descenders, so the clock can be drawn a third taller than the
text beside it without needing a taller row. That is why the clock reads as
larger even though its row is not.

**One typeface, one weight.** Nothing on a real indicator is bold, and
nothing is a different size. The apparent difference in the clock comes
entirely from filling the row rather than from a bigger font.

**The first departure carries no number.** Only the rotating row is
numbered — `3rd 14:51 Moorgate` — and the board counts no further than 3rd.

**No platform number.** You are standing on the platform.

**"Calling at:" does not move.** The label is fixed at the left and the list
of stations scrolls past it. In the photograph the label is followed by
`od Junction`, the middle of "Norwood Junction", caught mid-scroll.

## What this display does with them

The panel is 256x64, so it has more height than the reference board and the
same width. The rows are the same nine pixels; the extra height is shared out
as even spacing rather than spent on bigger text.

| | |
|---|---|
| Row height | 9px, all four rows |
| Row text | `Dot Matrix Regular`, 7px caps, descenders to 9px |
| Clock | `Dot Matrix Bold Tall` at 9px, digits filling all nine rows |
| Spacing | the leftover 28px shared evenly, so the gap above the first row matches the gap below the clock |

Both fonts are generated from the TTFs in `third_party/fonts` by
`third_party/font_to_py/font_to_py.py`; the command used is in the header of
each generated module in `src/assets`.

### A constraint worth knowing

These are dot-matrix typefaces with one native size each. Asking for anything
in between produces broken glyphs, because the strokes stop landing on whole
pixels. `Dot Matrix Regular` at 13px, next to the 9px it is designed for:

```
   13px             9px
   .####...        .###..
   ........        #...#.
   #....##.        #...#.
   #....##.        #####.
   ........        #...#.
   #######.        #...#.
   #....##.        #...#.
```

The gaps are not styling, they are the grid coming apart. So the row text
cannot simply be scaled up to fill more of the panel: 9px is what this
typeface renders cleanly, and the clock's 9px full-height digits come from
the `Bold Tall` face, which draws its digits to the full row by design.
