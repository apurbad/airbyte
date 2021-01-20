"""
MIT License

Copyright (c) 2020 Airbyte

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import traceback
from datetime import datetime
from typing import Generator, Mapping, Iterable

from airbyte_protocol import (
    AirbyteCatalog,
    AirbyteConnectionStatus,
    AirbyteMessage,
    AirbyteRecordMessage,
    AirbyteStream,
    ConfiguredAirbyteCatalog,
    Status,
    Type,
)
from base_python import Source, AirbyteLogger, ConfigContainer

from .client import Client


class SourceFile(Source):
    """This source aims to provide support for readers of different file formats stored in various locations.

    It is optionally using s3fs, gcfs or smart_open libraries to handle efficient streaming of very large files
    (either compressed or not).

    Supported examples of URL this can accept are as follows:
    ```
        s3://my_bucket/my_key
        s3://my_key:my_secret@my_bucket/my_key
        gs://my_bucket/my_blob
        azure://my_bucket/my_blob (not tested)
        hdfs:///path/file (not tested)
        hdfs://path/file (not tested)
        webhdfs://host:port/path/file (not tested)
        ./local/path/file
        ~/local/path/file
        local/path/file
        ./local/path/file.gz
        file:///home/user/file
        file:///home/user/file.bz2
        [ssh|scp|sftp]://username@host//path/file
        [ssh|scp|sftp]://username@host/path/file
        [ssh|scp|sftp]://username:password@host/path/file
    ```

    The source reader currently leverages `read_csv` but will be extended to readers of different formats for
    more potential sources as described below:
    https://pandas.pydata.org/pandas-docs/stable/user_guide/io.html
    - read_json
    - read_html
    - read_excel
    - read_feather
    - read_parquet
    - read_orc
    - read_pickle

    All the options of the readers are exposed to the configuration file of this connector so it is possible to
    override header names, types, encoding, etc

    Note that this implementation is handling `url` target as a single file at the moment.
    We will expand the capabilities of this source to discover and load either glob of multiple files,
    content of directories, etc in a latter iteration.
    """
    client_class = Client

    def _get_client(self, config: Mapping):
        """Construct client"""
        client = self.client_class(**config)

        return client

    def check(self, logger, config: Mapping) -> AirbyteConnectionStatus:
        """
        Check involves verifying that the specified file is reachable with
        our credentials.
        """
        client = self._get_client(config)
        storage = client.storage_scheme
        url = client.url
        logger.info(f"Checking access to {storage}{url}...")
        try:
            client.open_file_url()
            return AirbyteConnectionStatus(status=Status.SUCCEEDED)
        except Exception as err:
            reason = f"Failed to load {storage}{url}: {repr(err)}\n{traceback.format_exc()}"
            logger.error(reason)
            return AirbyteConnectionStatus(status=Status.FAILED, message=reason)

    def discover(self, logger: AirbyteLogger, config: Mapping) -> AirbyteCatalog:
        """
        Returns an AirbyteCatalog representing the available streams and fields in this integration. For example, given valid credentials to a
        Remote CSV File, returns an Airbyte catalog where each csv file is a stream, and each column is a field.
        """
        client = self._get_client(config)
        storage = client.storage_scheme
        url = client.url
        name = client.stream_name

        logger.info(f"Discovering schema of {name} at {storage}{url}...")
        try:
            streams = list(client.streams)
        except Exception as err:
            reason = f"Failed to discover schemas of {name} at {storage}{url}: {repr(err)}\n{traceback.format_exc()}"
            logger.error(reason)
            raise err
        return AirbyteCatalog(streams=streams)

    def read(self, logger: AirbyteLogger, config_container: ConfigContainer, catalog_path: str, state_path: str = None) -> Generator[
        AirbyteMessage, None, None]:
        """Returns a generator of the AirbyteMessages generated by reading the source with the given configuration, catalog, and state."""
        client = self._get_client(config)
        storage = client.storage_scheme
        url = client.url
        name = client.stream_name
        fields = self.selected_fields(catalog)

        logger.info(f"Reading {name} ({storage}{url})...")
        try:
            for row in client.read(fields=fields):
                record = AirbyteRecordMessage(stream=name, data=row, emitted_at=int(datetime.now().timestamp()) * 1000)
                yield AirbyteMessage(type=Type.RECORD, record=record)
        except Exception as err:
            reason = f"Failed to read data of {name} at {storage}{url}: {repr(err)}\n{traceback.format_exc()}"
            logger.error(reason)
            raise err

    @staticmethod
    def selected_fields(catalog: ConfiguredAirbyteCatalog) -> Iterable:
        for configured_stream in catalog.streams:
            yield from configured_stream.stream.json_schema["properties"].keys()
