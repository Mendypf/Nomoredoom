package io.github.mendypf.nomoredoom.core

/** A width and a height in pixels. */
public data class Size(val width: Int, val height: Int)

public object Fit {
    /**
     * The size at which a picture [width] by [height] fills a space [maxWidth] wide
     * without growing taller than [maxHeight]. It is scaled up or down, never
     * stretched. Null when there is no picture or no space.
     */
    public fun inside(width: Int, height: Int, maxWidth: Int, maxHeight: Int): Size? {
        if (width <= 0 || height <= 0 || maxWidth <= 0 || maxHeight <= 0) return null
        val tooTallAtFullWidth = height.toLong() * maxWidth > maxHeight.toLong() * width
        return if (tooTallAtFullWidth) {
            Size((width.toLong() * maxHeight / height).toInt().coerceAtLeast(1), maxHeight)
        } else {
            Size(maxWidth, (height.toLong() * maxWidth / width).toInt().coerceAtLeast(1))
        }
    }
}
