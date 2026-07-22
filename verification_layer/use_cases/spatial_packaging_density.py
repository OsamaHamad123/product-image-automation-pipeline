# verification_layer/use_cases/spatial_packaging_density.py
"""
Spatial Packaging & Volumetric Density Verification Engine.
Graham's Scan Convex Hull (O(N log N)) & Shoelace Polygon Area Formula.
Pure NumPy lightweight implementation.
"""

import numpy as np
from typing import Tuple
from verification_layer.domain.nextgen_models import PackagingDensityResult


def graham_scan_convex_hull(points: np.ndarray) -> np.ndarray:
    """
    Computes 2D Convex Hull of a set of points using Graham's Scan algorithm.
    """
    if len(points) < 3:
        return points

    # Find point with lowest y-coordinate (and lowest x if tie)
    start_idx = np.lexsort((points[:, 0], points[:, 1]))[0]
    start = points[start_idx]

    # Calculate angles and distances relative to start point
    diffs = points - start
    angles = np.arctan2(diffs[:, 1], diffs[:, 0])
    distances = np.hypot(diffs[:, 0], diffs[:, 1])

    # Sort points by angle, then by distance
    sort_idx = np.lexsort((distances, angles))
    sorted_pts = points[sort_idx]

    # Deduplicate collinear points
    unique_pts = [sorted_pts[0]]
    for p in sorted_pts[1:]:
        if len(unique_pts) > 1:
            p1, p2 = unique_pts[-2], unique_pts[-1]
            turn = (p2[0] - p1[0]) * (p[1] - p1[1]) - (p2[1] - p1[1]) * (p[0] - p1[0])
            if abs(turn) < 1e-9:
                unique_pts.pop()
        unique_pts.append(p)

    hull = [unique_pts[0], unique_pts[1]]
    for p in unique_pts[2:]:
        while len(hull) >= 2:
            p1, p2 = hull[-2], hull[-1]
            turn = (p2[0] - p1[0]) * (p[1] - p1[1]) - (p2[1] - p1[1]) * (p[0] - p1[0])
            if turn > 1e-9:  # Strict counter-clockwise turn
                break
            hull.pop()
        hull.append(p)

    return np.array(hull)


def calculate_shoelace_area(hull_points: np.ndarray) -> float:
    """Calculates area of polygon using Gauss's Shoelace formula."""
    if len(hull_points) < 3:
        return 0.0
    x_coords = hull_points[:, 0]
    y_coords = hull_points[:, 1]
    return 0.5 * float(np.abs(np.dot(x_coords, np.roll(y_coords, 1)) - np.dot(y_coords, np.roll(x_coords, 1))))


class SpatialPackagingDensityUseCase:
    def __init__(self, min_efficiency_ratio: float = 0.45):
        self.min_efficiency_ratio = min_efficiency_ratio

    def evaluate_mask_density(self, binary_mask: np.ndarray) -> PackagingDensityResult:
        y_indices, x_indices = np.where(binary_mask > 0)
        if len(x_indices) < 3:
            return PackagingDensityResult(
                hull_area=0.0,
                bbox_area=0.0,
                packaging_ratio=0.0,
                is_efficient=False,
                status_label="INSUFFICIENT_PRODUCT_PIXELS",
                hull_vertex_count=len(x_indices)
            )

        points = np.column_stack((x_indices, y_indices))
        hull = graham_scan_convex_hull(points)
        hull_area = calculate_shoelace_area(hull)

        min_x, max_x = int(np.min(x_indices)), int(np.max(x_indices))
        min_y, max_y = int(np.min(y_indices)), int(np.max(y_indices))

        bbox_width = max(1, max_x - min_x + 1)
        bbox_height = max(1, max_y - min_y + 1)
        bbox_area = float(bbox_width * bbox_height)

        packaging_ratio = round(float(hull_area / bbox_area), 4)
        is_efficient = packaging_ratio >= self.min_efficiency_ratio
        status_label = "OPTIMAL_PACKAGING_DENSITY" if is_efficient else "EXCESS_EMPTY_SPACE_LOOSE_PACKAGING"

        return PackagingDensityResult(
            hull_area=round(hull_area, 2),
            bbox_area=bbox_area,
            packaging_ratio=packaging_ratio,
            is_efficient=is_efficient,
            status_label=status_label,
            hull_vertex_count=len(hull)
        )
