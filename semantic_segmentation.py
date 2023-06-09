# -*- coding: utf-8 -*-

!pip install -q kaggle

import pandas as pd
import numpy as np
import os
import random

import matplotlib.pyplot as plt
import seaborn as sns

import cv2
from PIL import Image

import torch
from torchvision import transforms, utils
from torch.utils.data import Dataset, DataLoader

from google.colab import files

files.upload()

# !rm -r ~/.kaggle
!mkdir ~/.kaggle
!mv ./kaggle.json ~/.kaggle/
!chmod 600 ~/.kaggle/kaggle.json

!kaggle datasets download -d kumaresanmanickavelu/lyft-udacity-challenge

!unzip -q /content/lyft-udacity-challenge.zip -d /content/Dataset

!rm /content/lyft-udacity-challenge.zip

RGB_path = ['/content/Dataset/'+'data'+i+'/'+'data' +i+'/CameraRGB/' for i in ['A', 'B', 'C', 'D', 'E']]
seg_path  = ['/content/Dataset/'+'data'+i+'/'+'data'+i+'/CameraSeg/' for i in ['A', 'B', 'C', 'D', 'E']]

# Display dataset: Input image -> Expected output image
def display_image():
    
    for i in range(5):
        image_path = RGB_path[i]
        mask_path  = seg_path[i]
        
        image_name = random.choice(os.listdir(image_path))
        
        image = cv2.imread(os.path.join(image_path, image_name))
        mask  = cv2.imread(os.path.join(mask_path, image_name))
        
        figure, array = plt.subplots(1,2)
        array[0].imshow(image)
        array[0].set_title('RGB')
        array[1].imshow(mask[:,:,2])
        array[1].set_title('Mask')
        
display_image()

class preprocess_1(Dataset):
    '''
    Zip the input image with its appropriate output image
    '''
    def __init__(self, img_dir, mask_dir, transform=None):
        self.IMG_dir     = img_dir
        self.MASK_dir    = mask_dir
        self.transform   = transform
        self.img_names = []
        self.msk_names=[]
        
        if type(self.IMG_dir)==list:
            for i,j in zip(img_dir, mask_dir):
                for n in os.listdir(i):
                    self.img_names.append(os.path.join(i, n))
                    self.msk_names.append(os.path.join(j, n))
        else:
            for n in os.listdir(self.IMG_dir):
                    self.img_names.append(os.path.join(self.IMG_dir, n))
                    self.msk_names.append(os.path.join(self.MASK_dir, n))

    def __len__(self):
        return len(self.img_names) 
    
    def __getitem__(self, idx):
        img_name=self.img_names[idx]
        image=cv2.imread(img_name)
        mask_name=self.msk_names[idx]
        mask=cv2.imread(mask_name)
        
        sample={'image':image, 'mask':mask}
        
        if self.transform:
            sample['image']=self.transform(sample['image'])
            sample['mask']=self.transform(sample['mask'])
        
        
        return sample

def load_data():
    data_transforms = {
        'Train': transforms.Compose([transforms.ToPILImage(), transforms.Resize((256, 256)), transforms.ToTensor()]),
        'Test': transforms.Compose([transforms.ToPILImage(), transforms.Resize((256, 256)), transforms.ToTensor()]),
    }
    image_dataset = {
        'Train':preprocess_1(RGB_path[:-1], seg_path[:-1], transform=data_transforms['Train']),
        'Test':preprocess_1(RGB_path[-1], seg_path[-1], transform=data_transforms['Test'])
    }
    dataloader = {x: DataLoader(image_dataset[x], batch_size=4, shuffle=True, num_workers=8) for x in ['Train', 'Test']}
    
    return dataloader, image_dataset

dataloader, image_dataset = load_data()

from torchvision import models
from torchvision.models.segmentation.deeplabv3 import DeepLabHead

def create_DeepLabV3(outputchannels=1):
    
    my_model = models.segmentation.deeplabv3_resnet101(pretrained=True, progress=True)
    my_model.classifier = DeepLabHead(2048, outputchannels)
    my_model.train()
    
    return my_model

model = create_DeepLabV3(3)

import csv
import copy
import time
from tqdm.notebook import tqdm
from sklearn.metrics import f1_score, roc_auc_score

def train_model(model, criterion, dataloader, optimizer, metrics, bpath, num_epochs=3):
    START = time.time()
    best_wts = copy.deepcopy(model.state_dict())
    best_loss = 1e10
    
    # Use gpu if available
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)
    
    # Initialize the log file for training and testing loss and metrics
    field_names = ['Epoch', 'Train Loss', 'Test Loss'] + [f'Train_{m}' for m in metrics.keys()] + [f'Test_{m}' for m in metrics.keys()]
    with open(os.path.join(bpath, 'logs.csv'), 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=field_names)
        writer.writeheader()

    for epoch in range(1, num_epochs+1):
        print('Epoch {}/{}'.format(epoch, num_epochs))
        print('-' * 10)
        # Each epoch has a training and validation phase
        # Initialize batch summary
        batch_summary = {a: [0] for a in field_names}

        for phase in ['Train', 'Test']:
            if phase == 'Train':
                model.train()  # Set model to training mode
            else:
                model.eval()   # Set model to evaluate mode

            # Iterate over data.
            for sample in iter(dataloader[phase]):
                inputs = sample['image'].to(device)
                masks = sample['mask'].to(device)
                # zero the parameter gradients
                optimizer.zero_grad()

                # track history if only in train
                with torch.set_grad_enabled(phase == 'Train'):
                    outputs = model(inputs)
                    loss = criterion(outputs['out'], masks)
                    y_pred = outputs['out'].data.cpu().numpy().ravel()
                    y_true = masks.data.cpu().numpy().ravel()
        
                    for name, metric in metrics.items():
                        if name == 'f1_score':
                            # Use a classification threshold of 0.1
                            batch_summary[f'{phase}_{name}'].append(metric(y_true > 0, y_pred > 0.1))
                        else:
                            batch_summary[f'{phase}_{name}'].append(metric(y_true.astype('uint8'), y_pred))

                    # backward + optimize only if in training phase
                    if phase == 'Train':
                        loss.backward()
                        optimizer.step()
        
            batch_summary['epoch'] = epoch
            epoch_loss = loss
            batch_summary[f'{phase}_loss'] = epoch_loss.item()
            print('{} Loss: {:.4f}'.format(
                phase, loss))
        
        for field in field_names[3:]:
            batch_summary[field] = np.mean(batch_summary[field])
        
        print(batch_summary)
        
        with open(os.path.join(bpath, 'logs.csv'), 'a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, field_names=field_names)
            writer.writerow(batch_summary)
            # deep copy the model
            if phase == 'Test' and loss < best_loss:
                best_loss = loss
                best_wts = copy.deepcopy(model.state_dict())

    STOP = time.time()

    print('Training complete in {:.0f}m {:.0f}s'.format((STOP-START) // 60, (STOP-START) % 60))
    print('Lowest Loss: {:4f}'.format(best_loss))

    # load best model weights
    model.load_state_dict(best_wts)
    return model

epochs = 6
bpath = "/content/"

# Specify the loss function
criterion = torch.nn.MSELoss(reduction='mean')
# Specify the optimizer with a lower learning rate
optimizer = torch.optim.Adam(model.parameters(), lr=1e-5)

# Specify the evalutation metrics
metrics = {'f1_score': f1_score} #, 'auroc': roc_auc_score}

trained_model = train_model(model, criterion, dataloader,optimizer, bpath=bpath, metrics=metrics, num_epochs=epochs)

torch.save(trained_model, os.path.join(bpath, f'{epochs}_epochs_weights.pt'))

model_path = '/content/6epochs_weights.pt'

if torch.cuda.is_available():
    model = torch.load(model_path)
else:
    model = torch.load(model_path, map_location=torch.device('cpu'))
    
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

# Set the model to evaluate mode
model.eval()

original_Image = cv2.imread('/content/Dataset/dataA/dataA/CameraRGB/02_00_030.png')
image = cv2.resize(original_Image, (256, 256), cv2.INTER_AREA).transpose(2,0,1)
image = image.reshape(1, 3, image.shape[1],image.shape[2])

with torch.no_grad():
    if torch.cuda.is_available():
        a = model(torch.from_numpy(image).to(device).type(torch.cuda.FloatTensor)/255)
    else:
        a = model(torch.from_numpy(image).to(device).type(torch.FloatTensor)/255)

out_Image = a['out'].cpu().detach().numpy()[0]
out_Image = out_Image.transpose(1,2,0)[:,:,2]

figure, array = plt.subplots(1,3, figsize=(10,10))

array[0].imshow(cv2.resize(original_Image, (256, 256)))
array[0].set_title('Original Image')
array[0].axis('off')
array[1].imshow(cv2.resize(cv2.imread('/content/Dataset/dataA/dataA/CameraSeg/02_00_030.png'), (256, 256))[:,:,2])
array[1].set_title('True Mask')
array[1].axis('off')
array[2].imshow(outImage)
array[2].set_title('Predicted Mask')
array[2].axis('off')

