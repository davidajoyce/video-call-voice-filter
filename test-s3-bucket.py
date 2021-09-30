
import boto3
import librosa
import s3fs
import os
import random

client = boto3.client('s3')
s3 = boto3.resource('s3')
bucket = s3.Bucket('voicefilterdataset')
BUCKET_NAME = 'voicefilterdataset'
WAV_KEY_NAME = 'train-clean-100/911/130578/911-130578-0020-norm.wav'
FILE_NAME = '911-130578-0020-norm.wav'
srate = 16000

def main():
	print("Hello World!")

def test_s3_connection():
	objects = bucket.objects.all()
	for object in objects:
		print(object)

def downloadS3File():
	client.download_file(BUCKET_NAME, WAV_KEY_NAME, FILE_NAME)

def process_s3_objects():
	s3_response = client.get_object(Bucket=BUCKET_NAME, Key=WAV_KEY_NAME)
	object_content = s3_response['Body'].read()
	#time_series = librosa.load(object_content, sr=srate)
	#print(time_series)
	#fs = s3fs.S3FileSystem(anon=False)
	#with fs.open('voicefilterdataset/train-clean-100/911/130578/911-130578-0020-norm.wav', 'rb') as f:
	#	print(f.read())
	#	time_series = librosa.load(f, sr=srate)
	s3File = downloadS3File()
	librosa_load = librosa.load(FILE_NAME, sr=srate)
	print("librosa load")
	print(librosa_load)
	os.remove(FILE_NAME)

def s3_file_paths():
	files = []
	objects = bucket.objects.all()
	for obj in objects:
		files.append(obj.key)
	files_wav = [f for f in files if 'wav' in f]
	for file in files_wav:
		print(file)
	file_wav_dict = {}
	file_name_split_list = []
	for file_name in files_wav:
		file_name_split = file_name.split('/')[-1]
		file_wav_dict[file_name_split] = file_name
		file_name_split_list.append([file_name_split])
	#for file_name_split in file_name_split_list:
	#	print(file_wav_dict[file_name_split])
	spk1, spk2 = random.sample(file_name_split_list, 2)
	print('spk1',spk1)
	print('spk2',spk2)
	s1_dvec = random.sample(spk1, 2)
	print('s1_dvec', s1_dvec)

def output_s3_file():
	downloadS3File()
	object_name = 'output/911-130578-0020-norm.wav'
	client.upload_file(FILE_NAME, BUCKET_NAME, object_name)

if __name__ == "__main__":
	main()
	#test_s3_connection()
	process_s3_objects()
	s3_file_paths()
	output_s3_file()
