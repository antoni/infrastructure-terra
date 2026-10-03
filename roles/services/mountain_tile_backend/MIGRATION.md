# Migration from the previous role

Delete the old product-generation templates/tasks rather than carrying them forward:

- `map_style.json.j2`
- `metadata-*.json.j2`
- `tilejson-*.json.j2`
- vector/raster/terrain layer lists in defaults
- bootstrap release generation
- Nginx locations that expect expanded `{z}/{x}/{y}` directories

Retain the repository's existing `LICENSE` file unchanged when applying this role in-place. It is not reproduced in this generated ZIP because the original license text was not available as a file in this conversation.
