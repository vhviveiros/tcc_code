from image import LungMaskGenerator, ImageProcessor, ImageSaver, ImageCharacteristics
from utils import check_folder
from dataset_representation import Characteristics, CovidMaskDataset, CovidProcessedDataset,  NormalMaskDataset, NormalProcessedDataset


class Preprocessing:
    def __init__(self, img_target_size, img_input_size):
        """
        Initialize the Preprocessing object.

        Args:
            wandb (WandbUtils): The WandbUtils object to use for logging.
        """
        self.covid_mask_dataset = CovidMaskDataset()
        self.normal_masks = NormalMaskDataset()
        self.characteristics = Characteristics()
        self.img_target_size = img_target_size
        self.img_input_size = img_input_size

    def generate_lungs_masks(self, covid_artifact, normal_artifact):
        """
        Generate lung masks for the COVID and normal chest X-ray images.

        Args:
            covid_artifact (wandb.Artifact): The COVID chest X-ray images artifact.
            normal_artifact (wandb.Artifact): The normal chest X-ray images artifact.

        Returns:
            None
        """
        # For re-creating the folders
        check_folder(self.covid_mask_dataset.path)
        check_folder(self.normal_masks.path)

        # Generate masks
        LungMaskGenerator(folder_in=covid_artifact, folder_out=self.covid_mask_dataset.path,
                          target_size=self.img_target_size, input_size=self.img_input_size).generate()
        LungMaskGenerator(folder_in=normal_artifact, folder_out=self.normal_masks.path,
                          target_size=self.img_target_size, input_size=self.img_input_size).generate()

    def process_images(self, *artifacts):
        """
        Process the COVID and normal images, and save the processed images to the specified paths.

        Args:
            *artifacts: The COVID and normal chest X-ray images artifacts and their corresponding mask artifacts.
        Returns:
            None
        """
        # Load the dataset artifacts from wandb
        covid_artifact = artifacts[0]
        covid_mask_artifact = artifacts[1]
        normal_artifact = artifacts[2]
        normal_mask_artifact = artifacts[3]

        # Initialize the image processors with the dataset artifacts
        cov_processor = ImageProcessor(covid_artifact, covid_mask_artifact, target_size=self.img_target_size)
        normal_processor = ImageProcessor(normal_artifact, normal_mask_artifact, target_size=self.img_target_size)

        # Process the images
        print("Processing images\n")
        cov_processed = cov_processor.process()
        normal_processed = normal_processor.process()

        cov_processed_artifact = CovidProcessedDataset()
        normal_processed_artifact = NormalProcessedDataset()

        # Save the processed images
        cov_save_path = cov_processed_artifact.path
        normal_save_path = normal_processed_artifact.path

        # Create the save paths if they don't exist, and delete any previous
        check_folder(cov_save_path)
        check_folder(normal_save_path)

        # Save the processed images to the specified paths
        ImageSaver(cov_processed).save_to(cov_save_path)
        ImageSaver(normal_processed).save_to(normal_save_path)

    def generate_characteristics(self,
                                 cov_processed_artifact, normal_processed_artifact, cov_masks_artifact, normal_masks_artifact):
        """
        Generate image characteristics for the processed COVID and normal chest X-ray images.

        Args:
            cov_processed_artifact (wandb.Artifact): The processed COVID chest X-ray images artifact.
            normal_processed_artifact (wandb.Artifact): The processed normal chest X-ray images artifact.

        Returns:
            None
        """
        ic = ImageCharacteristics(cov_processed_artifact, normal_processed_artifact, self.img_target_size)
        ic.save(self.characteristics.path, cov_masks_artifact, normal_masks_artifact)
