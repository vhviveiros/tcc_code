import pandas as pd
from glob import glob
import cv2
import numpy as np
import os
import matplotlib.pyplot as plt
import mahotas as mt
from numba import njit, prange

from lung_seg_model import model

from utils import abs_path, load_config
from radiomics import featureextractor
import SimpleITK as sitk
import multiprocessing as mp
import logging
import tqdm
import math
import csv


class Image:
    def __init__(self, file_path, divide=False, reshape=False, target_size=(256, 256)):
        """
        Load an image file.

        Args:
            file_path (str): path to image file
            divide (bool): whether to divide the image by 255 after loading
            reshape (bool): whether to reshape the image to 1D array

        Attributes:
            path (str): path to image file
            divide (bool): whether to divide the image by 255 after loading
            reshape (bool): whether to reshape the image to 1D array
            data (ndarray): image data
        """
        self.file_path = file_path
        self.divide = divide
        self.reshape = reshape
        self.data = self.__load_file(target_size)

    def __load_file(self, target_size):
        """
        Load image file, preprocess and return the data.

        Args:
            target_size (tuple): target size to resize the image

        Returns:
            ndarray: preprocessed image data
        """
        # load image in grayscale
        img = cv2.imread(self.file_path, cv2.IMREAD_GRAYSCALE)

        # divide image by 255
        if self.divide:
            img = img / 255

        # resize image
        img = cv2.resize(img, target_size)

        # reshape image
        if self.reshape:
            img = np.reshape(img, img.shape + (1,))
            img = np.reshape(img, (1,) + img.shape)

        return img

    def get_filename(self):
        """
        Return filename.

        Returns:
            tuple: 0 = filename, 1 = file extension
        """
        return os.path.splitext(os.path.basename(self.file_path))

    def save_as_processed(self, out_path):
        """
        Save the image to a file, with 'processed' tag.

        Args:
            path_dir (str): directory to save the file
        """
        filename, fileext = self.get_filename()
        result_file = abs_path(out_path, "%s_processed%s" % (filename, fileext))
        cv2.imwrite(result_file, self.data)

    def shape(self):
        """
        Return the shape of the image data.

        Returns:
            tuple: shape of the image data
        """
        return self.data.shape

    def hist(self):
        """
        Calculate and return the histogram of the image data.

        Returns:
            ndarray: histogram of the image data
        """
        # calculate histogram
        result = np.squeeze(cv2.calcHist([self.data], [0], None, [255], [1, 256]))

        # convert result to integer type
        result = np.asarray(result, dtype='int32')

        return result

    def save_hist(self, save_folder=''):
        """
        Save the histogram of the image data to a file.

        Args:
            save_folder (str): directory to save the histogram file
        """
        # plot histogram
        plt.figure()
        histg = cv2.calcHist([self.data], [0], None, [254], [1, 255])
        plt.plot(histg)

        # save histogram to file
        filename, _ = self.get_filename()
        result_file = abs_path(save_folder, "%s_histogram%s" % (filename, '.png'))
        plt.savefig(result_file)
        plt.close()

    def haralick(self):
        """
        Calculate and return the mean of Haralick texture features for 4 types of adjacency.
        Returns:
            ndarray: Mean of the Haralick texture features.
        """
        # Calculate Haralick texture features for 4 types of adjacency
        textures = mt.features.haralick(self.data)

        # Take the mean of the Haralick texture features
        ht_mean = np.mean(textures, axis=0)

        return ht_mean

    def mahotas_characteristics(self):
        # Extract the Mahotas characteristics from the image data
        features = []

        # LBP features
        lbp = mt.features.lbp(self.data, 8, 8)
        features.extend(lbp)

        # Zernike moments
        zernike = mt.features.zernike(self.data, 10, 10)
        features.extend(zernike)

        # TAS features
        tas = mt.features.tas(self.data)
        features.extend(tas)

        return features


class ImageTuple:
    """This is a class that represents an image and its corresponding mask image.
    The mask image also has an associated image object, which allows for
    convenient access to the mask image's properties."""

    def __init__(self, image: Image, mask: Image):
        self.image = image
        self.mask = mask
        self.check_consistency()

    @staticmethod
    def from_image(image: Image, masks_dir_path: str, target_size):
        """This method creates an ImageTuple from an image and a masks directory.
        The masks directory is used to find the corresponding mask image for the input image."""
        img_filename = image.get_filename()
        mask_img_filename = "%s_mask%s" % (img_filename[0], img_filename[1])
        mask_img_path = "%s/%s" % (masks_dir_path, mask_img_filename)
        mask_img_path = mask_img_path.replace("_processed", "")  # Just in case of loading processed images
        mask = Image(mask_img_path, False, False, target_size=target_size)
        return ImageTuple(image, mask)

    def radiomics(self):
        # Convert numpy array to SimpleITK image
        sitk_image = sitk.GetImageFromArray(np.expand_dims(self.image.data, axis=0))
        sitk_mask = sitk.GetImageFromArray(np.expand_dims(self.mask.data, axis=0))

        # Create the feature extractor
        extractor = featureextractor.RadiomicsFeatureExtractor()

        # Run the feature extraction
        result = extractor.execute(imageFilepath=sitk_image, maskFilepath=sitk_mask)
        return list(result.values())[36:]

    def check_consistency(self):
        # Get the filenames of the image and the mask
        image_filename, image_extension = self.image.get_filename()
        mask_filename, mask_extension = self.mask.get_filename()

        if image_extension != mask_extension:
            raise ValueError(
                f"{image_filename}.{image_extension} and {mask_filename}.{mask_extension} have different extensions!")

        image_filename = image_filename.replace("_processed", "")
        if image_filename + "_mask" != mask_filename:
            # Raise an error telling that the mask doesnt represent the image:
            raise ValueError(
                f"The mask {mask_filename}.{mask_extension} is not valid for {image_filename}.{image_extension}!")


class ImageLoader:
    """
    A class for generating and preprocessing image data for COVID-19 detection.
    """

    def load_from(self, path: str, target_size, divide: bool = False, reshape: bool = False, only_data: bool = False, yield_len=False):
        """
        Generates an Image object from a given path.

        Parameters:
        ----------
        path : str
            Required. The path of the image file.
        divide : bool
            Optional. Default is False. Whether to divide the image into its RGB channels.
        reshape : bool
            Optional. Default is False. Whether to reshape the image into a specified shape.
        only_data : bool
            Optional. Default is False. Whether to return only the data of the image or the entire Image object.

        Returns:
        -------
        Image object or ndarray
            If only_data is True, returns the data of the image. Otherwise, returns the entire Image object.

        """
        image_files = glob(path + "/*g")

        if yield_len:
            yield len(image_files)

        for image_file in image_files:
            if only_data:
                yield Image(image_file, divide, reshape, target_size=target_size).data
            else:
                yield Image(image_file, divide, reshape, target_size=target_size)


class ImageSaver:
    def __init__(self, images):
        """
        Create an ImageSaver object with a list of Image objects to save.

        Args:
            images (list[Image]): A list of Image objects to save.
        """
        self.images = images

    def save_to(self, path_dir):
        """
        Save all Image objects in the ImageSaver object to the given directory.

        Args:
            path_dir (str): The directory to save the images in.
        """
        for img in self.images:
            img.save_as_processed(path_dir)


class ImageProcessor:
    def __init__(self, base_path: str, masks_path: str, target_size, divide: bool = False, reshape: bool = False, only_data: bool = False):
        self.base_path = base_path
        self.masks_path = masks_path

        print("Loading images...")
        # Load images:
        image_loader = ImageLoader().load_from(self.base_path, target_size, divide, reshape, only_data)
        self.tuples = list(map(lambda img: ImageTuple.from_image(
            img, self.masks_path, target_size=target_size), image_loader))
        print("Images loaded.")

    @staticmethod
    @njit(parallel=True)
    def __apply_mask(img_data, mask_data):
        """
        Apply mask to an image.

        Args:
        - img (ndarray): 2D array of shape (256, 256)
        - mask (ndarray): 2D array of shape (256, 256)

        Returns:
        - modified_img (ndarray): modified 2D array of shape (256, 256)
        """
        modified_img_data = np.copy(img_data)
        for i in prange(img_data.shape[0]):
            for j in prange(img_data.shape[1]):
                if mask_data[i, j] <= 20:
                    modified_img_data[i, j] = 0
        return modified_img_data

    def __process_image(self, img, mask):
        clahe = cv2.createCLAHE()
        eq_img_data = clahe.apply(img.data)
        processed_image_data = self.__apply_mask(eq_img_data, mask.data)
        img.data = processed_image_data
        # Return the Image object with the new processed data
        return img

    def process(self):
        return list(map(lambda tuple: self.__process_image(tuple.image, tuple.mask), self.tuples))


class LungMaskGenerator:
    """
    Class for segmenting lung images using the U-Net model.

    This code is based on the work presented in:
    https://www.kaggle.com/eduardomineo/u-net-lung-segmentation-montgomery-shenzhen/execution#4.-Results
    """

    def __init__(self, input_size=(256, 256, 1),
                 target_size=(256, 256),
                 folder_in='',
                 folder_out=''):
        """
        Initializes an LungMaskGenerator object.

        Args:
        - input_size: a tuple representing the input shape of the U-Net model.
        - target_size: a tuple representing the target shape of the input images.
        - folder_in: a string representing the path to the input folder containing the lung images.
        - folder_out: a string representing the path to the output folder where the masks will be saved.
        """
        self.input_size = input_size
        self.target_size = target_size
        self.folder_in = folder_in
        self.folder_out = folder_out

    def __load_image(self, img_file):
        """
        Loads and processes an image file.

        Args:
        - test_file: a string representing the path to the image file.

        Returns:
        - A numpy array representing the preprocessed image.
        """
        img = cv2.imread(img_file, cv2.IMREAD_GRAYSCALE)
        img = img / 255
        img = cv2.resize(img, self.target_size)
        img = np.reshape(img, img.shape + (1,))
        img = np.reshape(img, (1,) + img.shape)
        return img

    def __load_images(self, img_files):
        """
        Generator function that yields preprocessed image arrays.

        Args:
        - test_files: a list of strings representing the paths to the image files.

        Yields:
        - A numpy array representing the preprocessed image.
        """
        for img_file in img_files:
            yield self.__load_image(img_file)

    def __save_result(self, save_path, npyfile, test_files):
        """
        Saves the segmented images to disk.

        Args:
        - save_path: a string representing the path to the output folder.
        - npyfile: a numpy array representing the segmented images.
        - test_files: a list of strings representing the paths to the input image files.
        """
        for i, item in enumerate(npyfile):
            result_file = test_files[i]
            img = (item[:, :, 0] * 255.).astype(np.uint8)

            filename, fileext = os.path.splitext(os.path.basename(result_file))

            result_file = os.path.join(save_path, "%s_mask%s" % (filename, fileext))

            cv2.imwrite(result_file, img)

    def generate(self):
        """
        This method loads the saved model from disk, generates image predictions for the images in the specified input folder, and saves the segmented images to the specified output folder.

        :return: None
        """
        # Load saved model from disk
        model = model(input_size=self.input_size)
        model.load_weights('segmentation_model.hdf5')

        # Get list of image files from input folder
        files = glob(self.folder_in + "/*g")

        # Generate predictions for images in input folder
        gen = self.__load_images(files)  # TODO: check if this should be changed for the global loader
        results = model.predict_generator(gen, len(files), verbose=1)

        # Save segmented images to output folder
        self.__save_result(self.folder_out, results, files)


class ImageCharacteristics:
    def __init__(self, cov_images_artifact, normal_images_artifact, target_size):
        # TODO: Fix docstrings
        """
        Initializes an ImageCharacteristics object with a list of cov and non-cov images.

        Args:
        - cov_images (list): A list of Image objects representing the cov images.
        - normal_images (list): A list of Image objects representing the non-cov images.
        """
        loader = ImageLoader()
        self.cov_images = loader.load_from(cov_images_artifact,
                                           target_size,
                                           False, False, False, yield_len=True)
        self.cov_lenght = self.cov_images.__next__()
        self.normal_images = loader.load_from(normal_images_artifact,
                                              target_size,
                                              False, False, False, yield_len=True)
        self.normal_lenght = self.normal_images.__next__()

    def __extract_from_image(self, img: Image, label, mask_path):
        img_tuple = ImageTuple.from_image(img, mask_path, img.shape())
        yield from img.mahotas_characteristics()
        yield from img_tuple.radiomics()
        yield label

    def save(self, file_path, cov_masks_path, normal_masks_path):
        """
        Computes the histogram and texture features for each image and saves them to a csv file.

        Args:
        - file_path (str): The path to the file where the data will be saved.
        """
        logger = logging.getLogger("radiomics")
        logger.setLevel(logging.ERROR)
        # Define the number of worker processes to use
        num_workers = 24

        # Create a pool of worker processes
        pool = mp.Pool(num_workers)

        try:
            # Open the output file for writing
            with open(file_path, 'w') as f:
                writer = csv.writer(f)

                # Extract features from the normal images in parallel
                normal_progress = tqdm.tqdm(total=self.normal_lenght, desc='Extracting features from normal images')
                for img in self.normal_images:
                    features = self.__extract_from_image(img, 0, normal_masks_path)
                    writer.writerow(features)
                    normal_progress.update(1)

                # Extract features from the cov images in parallel
                cov_progress = tqdm.tqdm(total=self.cov_lenght, desc='Extracting features from cov images')
                for img in self.cov_images:
                    features = self.__extract_from_image(img, 1, cov_masks_path)
                    writer.writerow(features)
                    cov_progress.update(1)

        finally:
            # Close the worker processes
            pool.close()
            pool.join()


class ImageDataHistogram:

    @staticmethod
    def __hist_mean(images):
        """
        This function takes a list of images as an argument and returns the mean of the histograms of each image.
        It first creates a list of histograms for each image using the img.hist() method, then calculates the mean of all
        the histograms using the np.mean() method with axis=0 indicating that the mean should be calculated along the columns.
        Finally, it returns the mean value.
        """
        histograms = [img.hist() for img in images]
        hist_mean = np.mean(histograms, axis=0)
        return hist_mean

    @staticmethod
    def hist_mean(path, img_target_size):
        """
        This function takes in a path and returns the mean of the histogram of the image at that path.
        It generates an image using the ImageGenerator class and calls ImageDataHistogram's __hist_mean() method to calculate
        the mean of the histogram.
        """
        return ImageDataHistogram.__hist_mean(ImageLoader().load_from(
            abs_path(path), target_size=img_target_size))

    @staticmethod
    def __hist_median(images):
        """
        This function takes in a list of images (images) and returns the median of the histograms of each image (hist_median).
        It uses a list comprehension to create a list of the histograms for each image (histograms), then uses NumPy's median()
        function to calculate the median of those histograms, with axis=0 indicating that the median should be calculated along
        the first axis. The result is returned as hist_median.
        """
        histograms = [img.hist() for img in images]
        hist_median = np.median(histograms, axis=0)
        return hist_median

    @staticmethod
    def hist_median(path, img_target_size):
        """
        This function takes in a path to an image and returns the median of its histogram.
        It generates an image using the ImageGenerator class and calls the __hist_median() method of the ImageDataHistogram class
        to get the median of its histogram.
        """
        return ImageDataHistogram.__hist_median(ImageLoader().load_from(
            abs_path(path), target_size=img_target_size))
