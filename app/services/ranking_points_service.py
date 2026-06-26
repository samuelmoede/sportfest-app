def get_points_for_placement(placement_points, placement: int):
    if placement < 1 or placement > len(placement_points):
        return 0.0
    return placement_points[placement - 1]


def assign_points_by_placement_groups(
    rows,
    placement_points,
    *,
    placement_key: str,
    points_key: str,
):
    group_index = 0
    previous_placement = None

    for row in rows:
        placement = row[placement_key]
        if previous_placement is None or placement != previous_placement:
            group_index += 1
            previous_placement = placement
        row[points_key] = get_points_for_placement(placement_points, group_index)

    return rows
