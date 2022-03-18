import argparse
from rdflib import Graph, URIRef
import glob
import os
import _thread
from inotify_simple import INotify, flags
import http.server as http
from urllib.parse import urlparse, unquote, parse_qs
import json
import concurrent.futures
import time


# Process command line arguments
parser = argparse.ArgumentParser(description="Loads ttl-files and provides a HTTP interface (webserver) to them. The webserver automatically loads/removes any new/deleted file which is added/removed during runtime.")
parser.add_argument("--host", default="localhost", help="IP address which should be used (default: localhost)")
parser.add_argument("--port", type=int, default=8000, help="Port which should be used (default: 8000)")
parser.add_argument("--root_path", required=True, help="Path to directory with ttl-files which should be served.")
args = parser.parse_args()


inotify = INotify()
wd_to_path = {}  # maps watchdog to path
file_to_data = {}  # maps filepath to its triples


def load_data_from(file_path):
    """
    Loads the ttl, given by file_path and returns (file_path, data, triple_count)
    Data stores triples.
    """
    graph = Graph()
    graph.parse(file_path, format="ttl")

    data = {}
    triple_count = 0
    for subject, predicate, object in graph:
        triple_count += 1
        subject = subject
        object = object

        # add triple
        predicate_out = (predicate, "out")
        if subject not in data:
            data[subject] = {}
        if predicate_out not in data[subject]:
            data[subject][predicate_out] = []
        data[subject][predicate_out].append(object)

        # add backlink
        predicate_in = (predicate, "in")
        if object not in data:
            data[object] = {}
        if predicate_in not in data[object]:
            data[object][predicate_in] = []
        data[object][predicate_in].append(subject)
    print(file_path, ":", triple_count, "triples loaded")
    return (file_path, data, triple_count)


def load_files_from(dir_path):
    """
    Loads every file in dir_path and stores a watchdog in case the file changes to reload its content.
    The dir name is used as a graph name.
    """
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = []
        for ttl in glob.glob(dir_path + "/*.ttl"):
            if ttl not in file_to_data:
                wd = inotify.add_watch(ttl,  flags.CLOSE_WRITE)
                wd_to_path[wd] = ttl
                futures.append(executor.submit(load_data_from, ttl))
                dir_name = os.path.dirname(ttl)
                file_to_data[ttl] = {"graph_name": dir_name}
        for future in concurrent.futures.as_completed(futures):
            file_path, data, _ = future.result()
            file_to_data[file_path]["data"] = data


def get_data_for(resource_uri):
    """
    Returns all triples which are connected to resource_uri.
    For all URIs we searching for their labels using: http://www.w3.org/2000/01/rdf-schema#label
    """
    result = {}
    for data in file_to_data.values():
        if resource_uri in data["data"]:
            if data["graph_name"] not in result:
                result[data["graph_name"]] = []
            entries = result[data["graph_name"]]
            for (predicate, direction), objects in data["data"][resource_uri].items():
                predicate_data = {"predicate": predicate,
                                  "labels": get_label(predicate),
                                  "direction": direction,
                                  "objects": []}
                for object in objects:
                    object_data = {"uri": None, "labels": None, "literal": None, "language": None}
                    if type(object) == URIRef:
                        labels = get_label(object)
                        object_data["uri"] = str(object)
                        object_data["labels"] = labels
                    else:
                        object_data["literal"] = str(object)
                        object_data["language"] = object.language
                    predicate_data["objects"].append(object_data)
                entries.append(predicate_data)
    return result


def get_label(uri):
    """
    Returns all labels found for an URI using: http://www.w3.org/2000/01/rdf-schema#label
    """
    label_uri = URIRef('http://www.w3.org/2000/01/rdf-schema#label')
    labels = []
    for data in file_to_data.values():
        if uri in data["data"]:
            key = (label_uri, "out")
            if key in data["data"][uri]:
                for label in data["data"][uri][key]:
                    labels.append({"literal": str(label), "language": label.language})
    return labels


# first time loading data
print("start loading data")
wd = inotify.add_watch(args.root_path, flags.CREATE | flags.DELETE)
wd_to_path[wd] = args.root_path

folders = glob.glob(args.root_path+"/**/", recursive=True)
for folder in folders:
    wd = inotify.add_watch(folder, flags.CREATE | flags.DELETE)
    wd_to_path[wd] = folder

ttl_files = glob.glob(args.root_path+"/**/*.ttl", recursive=True)
for ttl_file in ttl_files:
    wd = inotify.add_watch(ttl_file,  flags.CLOSE_WRITE)
    wd_to_path[wd] = ttl_file

start = time.perf_counter()
total_triple_count = 0
with concurrent.futures.ProcessPoolExecutor() as executor:
    results = executor.map(load_data_from, ttl_files)
    for file_path, data, triple_count in results:
        file_to_data[file_path] = {"graph_name": os.path.dirname(file_path),
                                   "data": data}
        total_triple_count += triple_count
print("loading done! took:", time.perf_counter() - start, ",", total_triple_count, "triples loaded")


def listen_to_file_events():
    """
    handles the watchdogs for file changes and (re)loads content if a file was added or has changed.
    """
    while True:
        for event in inotify.read():
            # file was saved
            if event.mask & flags.CLOSE_WRITE:
                _, new_data = load_data_from(wd_to_path[event.wd])
                file_to_data[wd_to_path[event.wd]]["data"] = new_data
            # directory was created or deleted
            elif event.mask & flags.ISDIR:
                # directory was created, add new watchdog
                if event.mask & flags.CREATE:
                    ttl_folder = wd_to_path[event.wd] + event.name
                    wd = inotify.add_watch(ttl_folder, flags.CREATE | flags.DELETE)
                    wd_to_path[wd] = ttl_folder
                # directory was deleted remove watchdogs for directory and contained files
                elif event.mask & flags.DELETE:
                    folder_path = wd_to_path[event.wd] + event.name
                    for file_path in list(file_to_data.keys()):
                        if folder_path in file_path:
                            del file_to_data[file_path]

                    for wd, path in list(wd_to_path.items()):
                        if folder_path in path:
                            del wd_to_path[wd]
            # file was created or deleted
            elif not event.mask & flags.ISDIR:
                # file was created, add new watchdog
                if event.mask & flags.CREATE:
                    load_files_from(wd_to_path[event.wd])
                # file was deleted, remove watchdog
                elif event.mask & flags.DELETE:
                    file_name = wd_to_path[event.wd] +  event.name
                    del file_to_data[file_name]
                    for wd in list(wd_to_path.keys()):
                        if wd_to_path[wd] == file_name:
                            del wd_to_path[wd]


_thread.start_new_thread(listen_to_file_events, ())


class RequestHandler(http.BaseHTTPRequestHandler):
    """
    Uses http.BaseHTTPRequestHandler to provide a HTTP-server
    """

    def do_GET(self):
        """
        Returns based on the accept_headers the mainpage(main.html) or triple_data as json.
        main.html handles the search request via javascript.
        """
        accept_header = self.headers.get("Accept", "text/html")
        accepted_formats = list(map(lambda format: format.split(";")[0], accept_header.split(",")))
        for accepted_format in accepted_formats:
            if accepted_format == "application/json":
                return self.get_json()
            else:
                return self.get_mainpage()

    def get_json(self):
        """
        Returns the data found for a given URI as json
        """
        uri = parse_qs(urlparse(unquote(self.path)).query)["search"][0]
        resource_uri = URIRef(uri)
        content = json.dumps({"resource_uri": resource_uri,
                              "resource_label": get_label(resource_uri),
                              "data": get_data_for(resource_uri)
                              })
        return self.respond(content)

    def get_mainpage(self):
        """
        Returns the mainpage main.html
        """
        with open("html/main.html", "r") as f:
            file_content = f.read()
            return self.respond(file_content)

    def respond(self, content, status_code=200, content_type="text/html"):
        """
        Helperfunction to genereate a proper HTTP response.
        """
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(content.encode("utf-8") if type(content) == str else content)
        return


with http.ThreadingHTTPServer((args.host, args.port), RequestHandler) as server:
    server.serve_forever()
