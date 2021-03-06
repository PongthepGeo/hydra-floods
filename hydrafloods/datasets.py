from __future__ import absolute_import
import os
import ee
import math
import copy
import datetime
from pprint import pformat
from functools import partial
from ee.ee_exception import EEException
from hydrafloods import decorators, geeutils


class Dataset:
    """Base dataset class used to define an EE image collection by datetime and geographic region

    A dataset wraps an ee.ImageCollection by applying the spatial and temporal filtering upon init.
    Provides utility functionality to make working with and managing image collections less verbose

    Example:
        Create a dataset object for Sentinel-1 data over Alabama for 2019
        >>> ds = Dataset(
        ...    region = ee.Geometry.Rectangle([
                   -88.473227, 
                   30.223334, 
                   -84.88908, 
                   35.008028
               ]),
        ...    start_time = "2019-01-01",
        ...    end_time = "2020-01-01",
        ...    asset_id = "COPERNICUS/S1_GRD"
        ... )
        >>> ds 
        HYDRAFloods Dataset:
        {'asset_id': 'COPERNICUS/S1_GRD',
        'end_time': '2020-01-01',
        'name': 'Dataset',
        'region': [[[...], [...], [...], [...], [...]]],
        'start_time': '2019-01-01'}    
    """

    def __init__(self, region, start_time, end_time, asset_id, use_qa=False):
        """Initialize Dataset class

        args:
            region (ee.Geometry): earth engine geometry object to filter image collection by
            start_time (str | datetime.datetime): start time used to filter image collection
            end_time (str | datetime.datetime): end time used to filter image collection
            asset_id (str): asset id of earth engine collection
            use_qa (bool, optional): boolean to determine to use an internal function qa(). 
                Used for definining custom dataset objects

        raises:
            AttributeError: if qa() method is not defined and use_qa is True
        """

        # TODO: add exceptions to check datatypes
        self.region = region  # dtype = ee.Geometry
        self.start_time = start_time
        self.end_time = end_time
        self.asset_id = asset_id
        self.use_qa = use_qa

        # dictionary mapping of band names used to harmonized optical datasets to same names
        self.BANDREMAP = ee.Dictionary(
            {
                "landsat7": ee.List(["B1", "B2", "B3", "B4", "B5", "B7"]),
                "landsat8": ee.List(["B2", "B3", "B4", "B5", "B6", "B7"]),
                "viirs": ee.List(["M2", "M4", "I1", "I2", "I3", "M11"]),
                "sen2": ee.List(["B2", "B3", "B4", "B8", "B11", "B12"]),
                "modis": ee.List(
                    [
                        "sur_refl_b03",
                        "sur_refl_b04",
                        "sur_refl_b01",
                        "sur_refl_b02",
                        "sur_refl_b06",
                        "sur_refl_b07",
                    ]
                ),
                "new": ee.List(["blue", "green", "red", "nir", "swir1", "swir2"]),
            }
        )

        # get the image collection and filter by geographic region and date time
        imgcollection = (
            ee.ImageCollection(self.asset_id)
            .filterBounds(self.region)
            .filterDate(self.start_time, self.end_time)
        )

        # check if to apply arbitrary qa process on collection
        # qa function can be defined in custom objects extending dataset
        if self.use_qa:
            try:
                imgcollection = imgcollection.map(self.qa)
            except AttributeError:
                raise AttributeError("qa() method is not defined...please define one or set `use_qa` to False")
        
        
        self.collection = imgcollection

    def __repr__(self):
        # format datetime information
        if isinstance(self.start_time, datetime.datetime):
            ststr = self.start_time.strftime("%Y-%m-%d")
        else:
            ststr = self.start_time

        if isinstance(self.end_time, datetime.datetime):
            etstr = self.end_time.strftime("%Y-%m-%d")
        else:
            etstr = self.end_time

        # create dict of dataset information
        objDict = {
            "name": self.__class__.__name__,
            "asset_id": self.asset_id,
            "start_time": ststr,
            "end_time": etstr,
            "region": self.region.coordinates().getInfo(),
        }

        # pretty format dict and return information
        strRepr = pformat(objDict, depth=3)
        return f"HYDRAFloods Dataset:\n{strRepr}"

    @property
    def collection(self):
        """image collection object property wrapped by dataset
        """
        return self._collection

    @collection.setter
    def collection(self, value):
        """setter function for collection property
        """
        self._collection = value
        return

    @property
    def n_images(self):
        """Number of images contained in the dataset
        """
        return self.collection.size().getInfo()

    @property
    def dates(self):
        """Dates of imagery contained in the image collection
        """
        eeDates = self.collection.aggregate_array("system:time_start").map(
            lambda x: ee.Date(x).format("YYYY-MM-dd HH:mm:ss.SSS")
        )
        return eeDates.getInfo()

    def copy(self):
        """Returns a deep copy of the hydrafloods dataset class
        """
        return copy.deepcopy(self)

    def apply_func(self, func, inplace=False, *args, **kwargs):
        """Wrapper method to apply a function to all of the image in the dataset.
        Makes a copy of the collection and reassigns the image collection propety.
        Function must accept an ee.ImageCollection and return an ee.ImageCollection

        Args:
            func (object): Function to map across image collection. Function must accept ee.Image as first argument
            inplace (bool, optional): define whether to return another dataset object or update inplace. default = False
            **kwargs: arbitrary keyword to pass to `func`

        Returns:
            Dataset | None: copy of class with results from `func` as image within collection property
        """

        # get a partial function to map over imagery with the keywords applied
        # expects that the first positional arg is an
        func = partial(func, **kwargs)

        if inplace:
            self.collection = self.collection.map(func)
            return
        else:
            outCls = self.copy()
            outCls.collection = self.collection.map(func)
            return outCls

    def clip_to_region(self, inplace=False):
        """Clips all of the images to the geographic extent defined by region.
        Useful for setting geometries on unbounded imagery in collection (e.g. MODIS or VIIRS imagery)

        args:
            inplace (bool, optional): define whether to return another dataset object or update inplace. default = False

        returns:
            Dataset | None: returns dataset with imagery clipped to self.region or none depending on inplace
        """

        @decorators.carry_metadata
        def clip(img):
            """Closure function to perform the clipping while carrying metadata
            """
            return ee.Image(img.clip(self.region))

        if inplace:
            self.collection = self.collection.map(clip)
            return
        else:
            outCls = self.copy()
            outCls.collection = self.collection.map(clip)
            return outCls

    def merge(self, dataset, inplace=False):
        """Merge the collection of two datasets into one where self.collection will contain imagery from self and dataset arg.
        Results will be sorted by time

        args:
            dataset (Dataset): dataset object to merge
            inplace (bool, optional): define whether to return another dataset object or update inplace. default = False

        returns:
            Dataset | None: returns dataset where collection is merged imagery or none depending on inplace

        """
        merged = self.collection.merge(dataset.collection).sort("system:time_start")

        if inplace:
            self.collection = merged
            return
        else:
            outCls = self.copy()
            outCls.collection = merged
            return outCls

    def join(self, dataset, inplace=False):
        """Performs spatiotemporal join between self.collection and dataset.collection.
        Result will be a dataset where the collection is colocated imagery in space and time

        args:
            dataset (Dataset): dataset object to apply join with. Used as right in join operations 
            inplace (bool, optional): define whether to return another dataset object or update inplace. default = False

        returns:
            Dataset | None: returns dataset where collection with joined imagery or none depending on inplace

        """

        def _merge(img):
            """Closure func to take results from the join and combine into one image with overlaping region
            """
            join_coll = ee.ImageCollection.fromImages(img.get(key))

            img_geom = img.geometry(100)
            join_geom = join_coll.map(geeutils.get_geoms).union(100).geometry(100)
            overlap = img_geom.intersection(join_geom, 100)

            join_data = join_coll.mosaic()

            return img.addBands(join_data).clip(overlap)

        key = str(dataset.__class__.__name__)

        # get a time and space filter
        filter = ee.Filter.And(
            ee.Filter.maxDifference(
                **{
                    "difference": 1000 * 60 * 60 * 24,  # One day in milliseconds
                    "leftField": "system:time_start",
                    "rightField": "system:time_start",
                }
            ),
            ee.Filter.intersects(
                **{"leftField": ".geo", "rightField": ".geo", "maxError": 100}
            ),
        )
        # apply join on collections and save all results
        joined = ee.ImageCollection(
            ee.Join.saveAll(key).apply(
                primary=self.collection, secondary=dataset.collection, condition=filter
            )
        )

        # map over all filtered imagery, mosaic joined matches, and add bands to imagery
        joined = joined.map(_merge)

        if inplace:
            self.collection = joined
            return
        else:
            outCls = self.copy()
            outCls.collection = joined
            return outCls

    def aggregate_time(
        self, dates=None, period=1, reducer="mean", clip_to_area=False, inplace=False
    ):
        """Aggregates multiple images into one based on time periods and a user defined reducer.
        Useful for mosaicing images from same date or time period.
        Expects the images in this dataset to be homogenous (same band names and types)

        args:
            dates (list[str], optional): list of dates defined as beginning time period of aggregatation. default = None,
                all available uniques dates in collection will be used
            period (int, optional): number of days to advance from dates for aggregation. default = 1
            reducer (str | ee.Reducer, optional): reducer to apply to images for aggregation, accepts string reducer name 
                or ee.Reducer opbject, default = "mean"
            clip_to_area (bool): switch to clip imagery that has been merged to the overlaping region of imagery, default=False
            inplace (bool, optional): define whether to return another dataset object or update inplace. default = False

        returns:
            Dataset | None: returns dataset.collection with aggregated imagery or none depending on inplace

        """

        def _aggregation(d):
            """Closure function to map through days and reduce data within a given time period
            """
            t1 = ee.Date(d)
            t2 = t1.advance(period, "day")
            img = (
                self.collection.filterDate(t1, t2)
                .reduce(reducer)
                .rename(band_names)
                .set("system:time_start", t1.millis())
            )
            geom = (
                ee.FeatureCollection(
                    self.collection.filterDate(t1, t2).map(geeutils.get_geoms)
                )
                .union(100)
                .geometry(100)
            )
            outimg = ee.Algorithms.If(clip_to_area, img.clip(geom), img)

            return outimg

        if dates is None:
            dates = (
                self.collection.aggregate_array("system:time_start")
                .map(lambda x: ee.Date(x).format("YYYY-MM-dd"))
                .distinct()
            )
        else:
            dates = ee.List(dates)

        band_names = ee.Image(self.collection.first()).bandNames()

        out_coll = ee.ImageCollection.fromImages(dates.map(_aggregation))

        if inplace:
            self.collection = out_coll
            return
        else:
            outCls = self.copy()
            outCls.collection = out_coll
            return outCls

    @decorators.carry_metadata
    def band_pass_adjustment(self, img):
        """Method to apply linear band transformation to dataset image collection.
        Expects that dataset has properties `self.gain` and `self.bias` set

        args:
            img (ee.Image): image to apply regression on

        """
        # linear regression coefficients for adjustment
        return (
            img.multiply(self.gain)
            .add(self.bias)
            .set("system:time_start", img.get("system:time_start"))
        )


class Sentinel1(Dataset):
    """Class extending dataset for the Sentinel 1 collection
    """

    def __init__(self, *args, asset_id="COPERNICUS/S1_GRD", use_qa=True, **kwargs):
        """Initialize Sentinel1 Dataset class

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the Sentinel 1 earth engine collection. default="COPERNICUS/S1_GRD"
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Sentinel1, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        self.collection = self.collection.filter(
            ee.Filter.listContains("transmitterReceiverPolarisation", "VH")
        )

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for Sentinel2 surface reflectance dataset
        """
        angles = img.select("angle")
        return img.updateMask(angles.lt(45).And(angles.gt(30)))

    def add_fusion_features(self, inplace=False):
        """Method to add additional features to SAR imagery for data fusion.
        Will calculate Normalized Difference Polorization Index (VV-VH)/(VV+VH),
        VV/VH ratio, and categorical orbit bands

        returns:
            Dataset | None: returns dataset.collection where imagery has the added bands
        """

        def _add_fusion_features(img):
            """Closure function to add features as bands to the images
            """
            bounds = img.geometry(100)
            orbit = ee.String(img.get("orbitProperties_pass"))
            orbit_band = ee.Algorithms.If(
                orbit.compareTo("DESCENDING"), ee.Image(1), ee.Image(0)
            )
            vv = img.select("VV")
            vh = img.select("VH")
            ratio = vv.divide(vh).rename("ratio")
            ndpi = vv.subtract(vh).divide(vv.add(vh)).rename("ndpi")

            extraFeatures = ee.Image.cat(
                [ee.Image(orbit_band).rename("orbit"), ratio, ndpi]
            )

            return img.addBands(extraFeatures.clip(bounds))

        return self.apply_func(_add_fusion_features, inplace=inplace)


class Viirs(Dataset):
    def __init__(
        self,
        *args,
        asset_id="NOAA/VIIRS/001/VNP09GA",
        apply_band_adjustment=False,
        use_qa=True,
        **kwargs,
    ):
        """Initialize VIIRS Dataset class

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the VIIRS earth engine collection. default="NOAA/VIIRS/001/VNP09GA"
            apply_band_adjustment (bool, optional): boolean switch to apply linear band pass equation to convert values to Landsat8. default=False
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Viirs, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        # get the bands and rename to common optical names
        coll = self.collection.select(
            self.BANDREMAP.get("viirs"), self.BANDREMAP.get("new")
        )

        if apply_band_adjustment:
            # band bass adjustment coefficients taken calculated from https://code.earthengine.google.com/876f53861690e483fb3e3439a3571f27
            # slope coefficients
            self.gain = ee.Image.constant(
                [0.68328, 0.66604, 0.78901, 0.95324, 0.98593, 0.88941]
            )
            # y-intercept coefficients
            self.bias = ee.Image.constant(
                [0.016728, 0.030814, 0.023199, 0.036571, 0.026923, 0.021615]
            ).multiply(10000)
            coll = coll.map(self.band_pass_adjustment)

        self.collection = coll

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for VIIRS VNP09GA dataset
        """
        cloudMask = geeutils.extract_bits(
            img.select("QF1"), 2, end=3, new_name="cloud_qa"
        ).lt(1)
        shadowMask = geeutils.extract_bits(
            img.select("QF2"), 3, new_name="shadow_qa"
        ).Not()
        snowMask = geeutils.extract_bits(img.select("QF2"), 5, new_name="snow_qa").Not()
        sensorZenith = img.select("SensorZenith").abs().lt(6000)

        mask = cloudMask.And(shadowMask).And(sensorZenith)
        return img.updateMask(mask)


class Modis(Dataset):
    def __init__(self, *args, asset_id="MODIS/006/MOD09GA", use_qa=True, **kwargs):
        """Initialize MODIS Dataset class
        Can be used with MOD09GA and MYD09GA

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the MODIS earth engine collection. default="MODIS/006/MOD09GA"
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Modis, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        self.collection = self.collection.select(
            self.BANDREMAP.get("modis"), self.BANDREMAP.get("new")
        )

        self.clip_to_region(inplace=True)

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for MODIS MXD09GA dataset
        """
        qa = img.select("state_1km")
        cloudMask = geeutils.extract_bits(qa, 10, end=11, new_name="cloud_qa").lt(1)
        shadowMask = geeutils.extract_bits(qa, 2, new_name="shadow_qa").Not()
        snowMask = geeutils.extract_bits(qa, 12, new_name="snow_qa").Not()
        sensorZenith = img.select("SensorZenith").abs().lt(6000)
        mask = cloudMask.And(shadowMask).And(snowMask).And(sensorZenith)
        return img.updateMask(mask)


class Landsat8(Dataset):
    def __init__(self, *args, asset_id="LANDSAT/LC08/C01/T1_SR", use_qa=True, **kwargs):
        """Initialize Landsat8 Dataset class
        Can theoretically be useds with any Landsat surface reflectance collection (e.g. LANDSAT/LT05/C01/T1_SR)

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the Landsat earth engine collection. default="LANDSAT/LC08/C01/T1_SR"
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Landsat8, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        self.collection = self.collection.select(
            self.BANDREMAP.get("landsat8"), self.BANDREMAP.get("new")
        )

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for Landsat8 surface reflectance dataset
        """
        qa_band = img.select("pixel_qa")
        qaCloud = geeutils.extract_bits(qa_band, start=5, new_name="cloud_mask").eq(0)
        qaShadow = geeutils.extract_bits(qa_band, start=3, new_name="shadow_mask").eq(0)
        qaSnow = geeutils.extract_bits(qa_band, start=4, new_name="snow_mask").eq(0)
        mask = qaCloud.And(qaShadow).And(qaSnow)
        return img.updateMask(mask)


class Landsat7(Dataset):
    def __init__(
        self,
        *args,
        asset_id="LANDSAT/LE07/C01/T1_SR",
        apply_band_adjustment=False,
        use_qa=True,
        **kwargs,
    ):
        """Initialize Landsat7 Dataset class
        Can theoretically be useds with any Landsat surface reflectance collection (e.g. LANDSAT/LT05/C01/T1_SR)

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the Landsat7 earth engine collection. default="LANDSAT/LE07/C01/T1_SR"
            apply_band_adjustment (bool, optional): boolean switch to apply linear band pass equation to convert values to Landsat8. default=False
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Landsat7, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        coll = self.collection.select(
            self.BANDREMAP.get("landsat7"), self.BANDREMAP.get("new")
        )

        if apply_band_adjustment:
            # band bass adjustment coefficients taken from Roy et al., 2016 http://dx.doi.org/10.1016/j.rse.2015.12.024
            # slope coefficients
            self.gain = ee.Image.constant(
                [0.8474, 0.8483, 0.9047, 0.8462, 0.8937, 0.9071]
            )
            # y-intercept coefficients
            self.bias = ee.Image.constant(
                [0.0003, 0.0088, 0.0061, 0.0412, 0.0254, 0.0172]
            ).multiply(10000)
            coll = coll.map(self.band_pass_adjustment)

        self.collection = coll

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for Landsat7 surface reflectance dataset
        """
        qa_band = img.select("pixel_qa")
        qaCloud = geeutils.extract_bits(qa_band, start=5, new_name="cloud_mask").eq(0)
        qaShadow = geeutils.extract_bits(qa_band, start=3, new_name="shadow_mask").eq(0)
        qaSnow = geeutils.extract_bits(qa_band, start=4, new_name="snow_mask").eq(0)
        mask = qaCloud.And(qaShadow)  # .And(qaSnow)
        return img.updateMask(mask)


class Sentinel2(Dataset):
    def __init__(
        self,
        *args,
        asset_id="COPERNICUS/S2_SR",
        apply_band_adjustment=False,
        use_qa=True,
        **kwargs,
    ):
        """Initialize Sentinel2 Dataset class

        args:
            *args: positional arguments to pass to `Dataset` (i.e. `region`, `start_time`, `end_time`)
            asset_id (str): asset id of the Sentinel2 earth engine collection. default="COPERNICUS/S2_SR"
            apply_band_adjustment (bool, optional): boolean switch to apply linear band pass equation to convert values to Landsat8. default=False
            use_qa (bool, optional): boolean to determine to use a private `self.qa()` function. default=True
            **kwargs (optional): addtional arbitrary keywords to pass to `Dataset`
        """
        super(Sentinel2, self).__init__(*args, asset_id=asset_id, use_qa=use_qa, **kwargs)

        coll = self.collection.select(
            self.BANDREMAP.get("sen2"), self.BANDREMAP.get("new")
        )

        if apply_band_adjustment:
            # band bass adjustment coefficients taken HLS project https://hls.gsfc.nasa.gov/algorithms/bandpass-adjustment/
            # slope coefficients
            self.gain = ee.Image.constant(
                [0.9778, 1.0053, 0.9765, 0.9983, 0.9987, 1.003]
            )
            # y-intercept coefficients
            self.bias = ee.Image.constant(
                [-0.00411, -0.00093, 0.00094, -0.0001, -0.0015, -0.0012]
            ).multiply(10000)
            coll = coll.map(self.band_pass_adjustment)

        self.collection = coll

        return

    @decorators.carry_metadata
    def qa(self, img):
        """Custom QA masking method for Sentinel2 surface reflectance dataset
        """
        sclImg = img.select("SCL")  # Scene Classification Map
        mask = sclImg.gte(4).And(sclImg.lte(6))
        return img.updateMask(mask)
