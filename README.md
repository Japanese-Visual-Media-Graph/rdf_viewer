# rdf_viewer

Small program to load ttls and present them in a browser. If you
search for an URI rdf_viewer searches for all triples which are
connected to this URI. For each URI rdf_viewer also tries to find
labels using http://www.w3.org/2000/01/rdf-schema#label

Only tested on Linux! 
Not intended for public hosting!
Uses [http.BaseHTTPRequestHandler](https://docs.python.org/3/library/http.server.html) from python so be careful regarding security!
Be careful about RAM usage, 4.9 million triples result in ~3.5 GB used! Can be higher while loading data!

## Dependencies
- [rdflib](https://github.com/RDFLib/rdflib)
- [inotify_simple](https://inotify-simple.readthedocs.io/en/latest/)

## Usage

- **--host** IP address which should be used. default: localhost
- **--port** Port which should be used. default: 8000
- **--root_path** path to ttls, has to be a directory. All
  subdirectories are also loaded. You can add/delete or change ttls
  and rdf_viewer will automatically reload them if necessary.

### Example
~~~
python rdf_viewer.py --root_path path/to/ttls/
~~~


