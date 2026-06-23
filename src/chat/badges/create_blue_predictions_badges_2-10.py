def create_blue_prediction_svg(number: int, filename: str):
    # For two-digit numbers (10), use a slightly smaller font
    font_size = 11.5 if number >= 10 else 14.6667
    y_offset = 14.8 if number >= 10 else 14.335299
    
    svg_template = f'''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg
   viewBox="0 0 18 18"
   width="18"
   height="18"
   version="1.1"
   id="svg1"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
  <defs id="defs1" />
  <circle
     style="fill:#1c71d8;stroke-width:5;stroke-linecap:round;stroke-linejoin:bevel;paint-order:markers stroke fill"
     id="path3"
     cx="9"
     cy="9"
     r="8" />
  <text
     xml:space="preserve"
     style="font-weight:bold;font-size:{font_size}px;font-family:'Adwaita Sans';-inkscape-font-specification:'Adwaita Sans Bold';text-align:center;writing-mode:lr-tb;direction:ltr;text-anchor:middle;fill:#ffffff;fill-opacity:1;stroke-width:5;stroke-linecap:round;stroke-linejoin:bevel;paint-order:markers stroke fill"
     x="9"
     y="{y_offset}"
     id="text3">{number}</text>
</svg>
'''
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(svg_template)

# Generate files for 2 to 10
for i in range(2, 11):
    filename = f"predictions-blue-{i}.svg"
    create_blue_prediction_svg(i, filename)
    print(f"Created {filename}")
