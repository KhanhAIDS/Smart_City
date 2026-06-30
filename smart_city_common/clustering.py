from collections import deque
import math

def compute_crowd_clusters(detections: list, size_ratio_min: float, distance_factor: float, min_cluster_size: int = 2) -> list[dict]:
    """
    Computes crowd clusters from a list of detections.
    Returns a list of clusters sorted by size descending.
    Each cluster is a dict: {"size": int, "bbox": [x1, y1, x2, y2], "member_indices": list[int]}
    """
    if not detections:
        return []

    valid_indices = []
    feet = []
    heights = []

    for i, d in enumerate(detections):
        b = d.get("bbox")
        if not b or len(b) != 4:
            continue
        
        try:
            x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        except (ValueError, TypeError):
            continue
            
        valid_indices.append(i)
        feet.append(((x1 + x2) / 2.0, y2))
        heights.append(max(y2 - y1, 1.0))

    n = len(valid_indices)
    if n == 0:
        return []

    def is_neighbor(i, j):
        hi, hj = heights[i], heights[j]
        if min(hi, hj) / max(hi, hj) < size_ratio_min:
            return False
        dx = feet[i][0] - feet[j][0]
        dy = feet[i][1] - feet[j][1]
        dist = math.sqrt(dx * dx + dy * dy)
        return dist <= (hi + hj) / 2.0 * distance_factor

    adj = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if is_neighbor(i, j):
                adj[i].append(j)
                adj[j].append(i)

    visited = set()
    clusters = []

    for i in range(n):
        if i in visited:
            continue
        
        q = deque([i])
        visited.add(i)
        
        cluster_local_indices = []
        while q:
            curr = q.popleft()
            cluster_local_indices.append(curr)
            for nb in adj[curr]:
                if nb not in visited:
                    visited.add(nb)
                    q.append(nb)
                    
        member_indices = [valid_indices[idx] for idx in cluster_local_indices]
        
        # Calculate cluster bounding box
        cluster_boxes = [detections[idx].get("bbox") for idx in member_indices]
        cluster_bbox = [
            min(float(box[0]) for box in cluster_boxes),
            min(float(box[1]) for box in cluster_boxes),
            max(float(box[2]) for box in cluster_boxes),
            max(float(box[3]) for box in cluster_boxes),
        ]
        
        clusters.append({
            "size": len(member_indices),
            "bbox": cluster_bbox,
            "member_indices": member_indices
        })

    # Filter clusters by minimum size
    clusters = [c for c in clusters if c["size"] >= min_cluster_size]

    # Sort clusters by size descending
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters
