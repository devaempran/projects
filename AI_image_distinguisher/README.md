# Distinguishing AI-Generated Images from Real Photography
By: Akin Akinlabi, Cat Weiss, Deva Empranthiri, Gia Nguyen

Raw dataset can be downloaded from https://www.kaggle.com/datasets/tristanzhang32/ai-generated-images-vs-real-images/data?select=train

Data is saved in AWS S3 and is run on AWS EC2 Instances due to limited local computational power. A snippet of what the data looks like is in data/raw/ which includes 10 images from each classes for our train and test sets.

Order of script execution:
1. preprocessing: pull the data from an S3 bucket, preprocess the images, save it back as numpy batches
2. eda: explore raw dataset and generate graphs
3. baseline_model: dummy classifier
4. simple_CNN: simple CNN model
5. tuned_model: CNN model with hyperparameter tuning

model_eval script is used by the simple_CNN script to evaluate on the training and validation set.

The history.json file stores the accuracy and loss of the model.

Final Presentation is named "207 Final Presentation.pdf"
