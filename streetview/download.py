import asyncio
import concurrent.futures
import itertools
import time
from dataclasses import dataclass
from io import BytesIO
from typing import AsyncGenerator, Generator, Tuple

import httpx
import requests
from PIL import Image
import json

async_client = httpx.AsyncClient()


DEFAULT_MAX_RETRIES = 6


@dataclass
class TileInfo:
    x: int
    y: int
    fileurl: str


@dataclass
class Tile:
    x: int
    y: int
    image: Image.Image


def get_width_and_height_from_zoom(pano_id, zoom: int) -> Tuple[int, int]:
    """
    Returns the width and height of a panorama at a given zoom level, depends on the
    zoom level.
    """
    photometa_document = json.loads(
    requests.get(
        url="https://www.google.com/maps/photometa/v1?authuser=0&hl=en&gl=us&pb=!1m4!1smaps_sv.tactile!11m2!2m1!1b1!2m2!1sen!2sus!3m3!1m2!1e2!2s%s!4m57!1e1!1e2!1e3!1e4!1e5!1e6!1e8!1e12!2m1!1e1!4m1!1i48!5m1!1e1!5m1!1e2!6m1!1e1!6m1!1e2!9m36!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e3!2b1!3e2!1m3!1e3!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e1!2b0!3e3!1m3!1e4!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e3"
        % (pano_id)
    ).text[4:]
    )

    width = int(photometa_document[1][0][2][2][0] / pow(2, 4 - zoom))
    height = int(photometa_document[1][0][2][2][0] / pow(2, 5 - zoom))



    return width/512, height/512


def make_download_url(pano_id: str, zoom: int, x: int, y: int) -> str:
    """
    Returns the URL to download a tile.
    """
    return (
        "https://cbk0.google.com/cbk"
        f"?output=tile&panoid={pano_id}&zoom={zoom}&x={x}&y={y}"
    )


def fetch_panorama_tile(
    tile_info: TileInfo, max_retries: int = DEFAULT_MAX_RETRIES
) -> Image.Image:
    """
    Tries to download a tile, returns a PIL Image.
    """
    for _ in range(max_retries):
        try:
            response = requests.get(tile_info.fileurl, stream=True)
            return Image.open(BytesIO(response.content))
        except requests.ConnectionError:
            print("Connection error. Trying again in 2 seconds.")
            time.sleep(2)
    raise requests.ConnectionError("Max retries exceeded.")


async def fetch_panorama_tile_async(
    tile_info: TileInfo, max_retries: int = DEFAULT_MAX_RETRIES
) -> Image.Image:
    """
    Asynchronously tries to download a tile, returns a PIL Image.
    """
    for _ in range(max_retries):
        try:
            response = await async_client.get(tile_info.fileurl)
            return Image.open(BytesIO(response.content))

        except httpx.RequestError as e:
            print(f"Request error {e}. Trying again in 2 seconds.")
            await asyncio.sleep(2)

    raise httpx.RequestError("Max retries exceeded.")


def iter_tile_info(pano_id: str, zoom: int) -> Generator[TileInfo, None, None]:
    """
    Generate a list of a panorama's tiles and their position.
    """
    width, height = get_width_and_height_from_zoom(pano_id, zoom)
    for x, y in itertools.product(range(width), range(height)):
        yield TileInfo(
            x=x,
            y=y,
            fileurl=make_download_url(pano_id=pano_id, zoom=zoom, x=x, y=y),
        )


def iter_tiles(
    pano_id: str,
    zoom: int,
    max_retries: int = DEFAULT_MAX_RETRIES,
    multi_threaded: bool = False,
) -> Generator[Tile, None, None]:
    if not multi_threaded:
        for info in iter_tile_info(pano_id, zoom):
            image = fetch_panorama_tile(info, max_retries)
            yield Tile(x=info.x, y=info.y, image=image)
        return

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_tile = {
            executor.submit(fetch_panorama_tile, info, max_retries): info
            for info in iter_tile_info(pano_id, zoom)
        }
        for future in concurrent.futures.as_completed(future_to_tile):
            info = future_to_tile[future]
            try:
                image = future.result()
            except Exception as exc:
                raise Exception(
                    f"Failed to download tile {info.fileurl} due to Exception: {exc}"
                )
            else:
                yield Tile(x=info.x, y=info.y, image=image)


async def iter_tiles_async(
    pano_id: str, zoom: int, max_retries: int = DEFAULT_MAX_RETRIES
) -> AsyncGenerator[Tile, None]:
    for info in iter_tile_info(pano_id, zoom):
        image = await fetch_panorama_tile_async(info, max_retries)
        yield Tile(x=info.x, y=info.y, image=image)
    return


def get_panorama(
    pano_id: str,
    zoom: int = 5,
    multi_threaded: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Image.Image:
    """
    Downloads a streetview panorama.
    Multi-threaded is a lot faster, but it's also a lot more likely to get you banned.
    """
    tile_width = 512
    tile_height = 512

    total_width, total_height = get_width_and_height_from_zoom(pano_id, zoom)
    panorama = Image.new("RGB", (total_width * tile_width, total_height * tile_height))

    for tile in iter_tiles(
        pano_id=pano_id,
        zoom=zoom,
        multi_threaded=multi_threaded,
        max_retries=max_retries,
    ):
        panorama.paste(im=tile.image, box=(tile.x * tile_width, tile.y * tile_height))
        del tile

    return panorama


async def get_panorama_async(
    pano_id: str, zoom: int, max_retries: int = DEFAULT_MAX_RETRIES
) -> Image.Image:
    """
    Downloads a streetview panorama by iterating through the tiles asynchronously.
    This runs in about the same speed as `get_panorama` with `multi_threaded=True`.
    """
    tile_width = 512
    tile_height = 512

    total_width, total_height = get_width_and_height_from_zoom(pano_id, zoom)
    panorama = Image.new("RGB", (total_width * tile_width, total_height * tile_height))

    async for tile in iter_tiles_async(
        pano_id=pano_id, zoom=zoom, max_retries=max_retries
    ):
        panorama.paste(im=tile.image, box=(tile.x * tile_width, tile.y * tile_height))
        del tile

    return panorama
