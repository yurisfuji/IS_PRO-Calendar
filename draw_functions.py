def lighten_color(color, factor=0.3):
    """Осветляет цвет, добавляя белого"""
    r, g, b = color
    return (
        min(255, int(r + (255 - r) * factor)),
        min(255, int(g + (255 - g) * factor)),
        min(255, int(b + (255 - b) * factor))
    )


def darken_color(color, factor=0.3):
    """Затемняет цвет, уменьшая его яркость"""
    r, g, b = color
    return (
        max(0, int(r * (1 - factor))),
        max(0, int(g * (1 - factor))),
        max(0, int(b * (1 - factor)))
    )


def draw_rounded_rectangle(draw, xy, radius=5, fill=None, outline=None, width=1):
    """Рисует прямоугольник со скругленными углами"""
    x1, y1, x2, y2 = xy

    corrected_radius = radius if (y2 - y1) / radius > 3 else radius / 3

    # Основной прямоугольник (без углов)
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=None)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=None)

    # Углы - рисуем круги в каждом углу
    draw.ellipse([x1, y1, x1 + radius * 2, y1 + radius * 2], fill=fill, outline=None)
    draw.ellipse([x2 - radius * 2, y1, x2, y1 + radius * 2], fill=fill, outline=None)
    draw.ellipse([x1, y2 - radius * 2, x1 + radius * 2, y2], fill=fill, outline=None)
    draw.ellipse([x2 - radius * 2, y2 - radius * 2, x2, y2], fill=fill, outline=None)

    # Обводка (если нужна)
    if outline and width > 0:
        # Вертикальные линии
        draw.line([x1, y1 + radius, x1, y2 - radius], fill=outline, width=width)
        draw.line([x2, y1 + radius, x2, y2 - radius], fill=outline, width=width)

        # Горизонтальные линии
        draw.line([x1 + radius, y1, x2 - radius, y1], fill=outline, width=width)
        draw.line([x1 + radius, y2, x2 - radius, y2], fill=outline, width=width)

        # Дуги в углах
        draw.arc([x1, y1, x1 + corrected_radius * 2, y1 + corrected_radius * 2], 180, 270, fill=outline, width=width)
        draw.arc([x2 - corrected_radius * 2, y1, x2, y1 + corrected_radius * 2], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - corrected_radius * 2, x1 + corrected_radius * 2, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2 - corrected_radius * 2, y2 - corrected_radius * 2, x2, y2], 0, 90, fill=outline, width=width)


def draw_dotted_line(draw, xy, fill, width=1, dot_interval=5):
    """Рисует точечную линию"""
    x1, y1, x2, y2 = xy

    # Вычисляем длину линии
    line_length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    # Количество точек
    num_dots = int(line_length / dot_interval)

    for i in range(num_dots):
        ratio = i * dot_interval / line_length
        x = x1 + (x2 - x1) * ratio
        y = y1 + (y2 - y1) * ratio

        # Рисуем точку как маленький прямоугольник
        dot_size = width
        draw.rectangle(
            [x - dot_size // 2, y - dot_size // 2, x + dot_size // 2, y + dot_size // 2],
            fill=fill
        )


def hex_to_rgb(hex_color):
    """
    Преобразует HEX цвет в RGB tuple
    Пример: "#FF0000" → (255, 0, 0)
    """
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    elif len(hex_color) == 3:
        return tuple(int(hex_color[i:i + 1] * 2, 16) for i in (0, 1, 2))
    else:
        raise ValueError(f"Неверный формат HEX цвета: {hex_color}")


def rgb_to_hex(rgb):
    """
    Преобразует RGB tuple в HEX цвет
    Пример: (255, 0, 0) → "#FF0000"
    """
    return '#{:02x}{:02x}{:02x}'.format(*rgb).upper()