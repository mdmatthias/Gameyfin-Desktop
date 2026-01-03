import sys
from PyQt6.QtGui import QImage, QPainter, QColor
from PyQt6.QtCore import QRect

def make_square(input_path, output_path, size=256):
    image = QImage(input_path)
    if image.isNull():
        print(f"Failed to load image: {input_path}")
        sys.exit(1)

    # Create a new square image with transparent background
    square_image = QImage(size, size, QImage.Format.Format_ARGB32)
    square_image.fill(QColor(0, 0, 0, 0))

    painter = QPainter(square_image)
    
    # Calculate centering position
    x = (size - image.width()) // 2
    y = (size - image.height()) // 2
    
    painter.drawImage(x, y, image)
    painter.end()

    if not square_image.save(output_path):
        print(f"Failed to save image: {output_path}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 make_square_icon.py <input_image> <output_image>")
        sys.exit(1)
    
    make_square(sys.argv[1], sys.argv[2])
