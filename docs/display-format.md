# How a platform indicator is laid out

Notes from measuring a photograph of a real dot-matrix platform indicator,
and what this display does with them.

![the board](images/platform_indicator.png)

*A real Chiltern Railways morning from Stoke Mandeville, as the API returned
it. It is the board baked into the firmware, in `src/fallback.py`. The amber
is `#FF9900`: hue 35 degrees, the dominant hue of the lit pixels in the
photograph below.*

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

**The clock's hours and minutes use all nine.** They are the only thing on
the board that does. Digits have no descenders, so they can be drawn a third
taller than the text beside them without needing a taller row. That is why
the clock reads as larger even though its row is not.

**Its seconds are shorter but not thinner.** Seven pixel rows rather than
nine, sitting on the bottom edge of the tall ones, and drawn with the same
double-thickness strokes: counted off the photograph, a seconds `0` is seven
pixels each way around a 3x5 hole, so its sides are two pixels and its top
and bottom one. The row text's `0`, at the same height, has single-pixel
walls. That is what makes the clock read as a time with its seconds appended
rather than as two different weights.

**Its colons are widely spaced, and its digits evenly so.** Three pixel rows
between the two dots with one clear above and below, rather than the single
row a colon gets in running text; and a `1` takes the same room as an `8`,
which is what stops the colons shifting as the time changes.

**One typeface, one weight.** Nothing on a real indicator is bold. The
difference in the clock comes from filling the row and from the seconds being
the shorter digits, not from a heavier font.

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
| Clock | `Dot Matrix Bold Tall` at 9px for the hours and minutes, `Dot Matrix Bold` at 10px for the seconds, two rows down to share their bottom edge |
| Clock digits | each on the widest digit's cell and centred in it, so the row keeps still as it ticks |
| Clock colons | drawn rather than set from the font, three rows between the dots |
| Spacing | the leftover 28px shared evenly, so the gap above the first row matches the gap below the clock |

All three fonts are generated from the TTFs in `third_party/fonts` by
`third_party/font_to_py/font_to_py.py`; the command used is in the header of
each generated module in `src/assets`. `Dot Matrix Bold` renders 7 pixels tall
at 10px, with the double-thickness strokes the seconds want; its `0` is six
pixels of ink around a 2x5 hole rather than the photograph's seven around 3x5,
because 11px is the next size up and it breaks the grid, dropping a whole row
out of every digit.

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
