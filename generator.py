import os
import glob
import tqdm
import torch
import random
import librosa
import argparse
import numpy as np
import boto3
import s3fs
import soundfile
from multiprocessing import Pool, cpu_count

from utils.audio import Audio
from utils.hparams import HParam


BUCKET_NAME = 'voicefilterdataset'
s3_client = boto3.client('s3')
s3 = boto3.resource('s3')
bucket = s3.Bucket('voicefilterdataset')
fs = s3fs.S3FileSystem(anon=False)

def formatter(dir_, form, num):
    return os.path.join(dir_, form.replace('*', '%06d' % num))

def vad_merge(w):
    intervals = librosa.effects.split(w, top_db=20)
    temp = list()
    for s, e in intervals:
        temp.append(w[s:e])
    return np.concatenate(temp, axis=None)


def mix(hp, args, audio, num, s1_dvec, s1_target, s2, train):
    srate = hp.audio.sample_rate
    dir_ = os.path.join(args.out_dir, 'train' if train else 'test')

    d, _ = librosa.load(s1_dvec, sr=srate)
    w1, _ = librosa.load(s1_target, sr=srate)
    w2, _ = librosa.load(s2, sr=srate)
    assert len(d.shape) == len(w1.shape) == len(w2.shape) == 1, \
        'wav files must be mono, not stereo'

    d, _ = librosa.effects.trim(d, top_db=20)
    w1, _ = librosa.effects.trim(w1, top_db=20)
    w2, _ = librosa.effects.trim(w2, top_db=20)

    # if reference for d-vector is too short, discard it
    if d.shape[0] < 1.1 * hp.embedder.window * hp.audio.hop_length:
        return

    # LibriSpeech dataset have many silent interval, so let's vad-merge them
    # VoiceFilter paper didn't do that. To test SDR in same way, don't vad-merge.
    if args.vad == 1:
        w1, w2 = vad_merge(w1), vad_merge(w2)

    # I think random segment length will be better, but let's follow the paper first
    # fit audio to `hp.data.audio_len` seconds.
    # if merged audio is shorter than `L`, discard it
    L = int(srate * hp.data.audio_len)
    if w1.shape[0] < L or w2.shape[0] < L:
        return
    w1, w2 = w1[:L], w2[:L]

    mixed = w1 + w2

    norm = np.max(np.abs(mixed)) * 1.1
    w1, w2, mixed = w1/norm, w2/norm, mixed/norm

    # save vad & normalized wav files
    target_wav_path = formatter(dir_, hp.form.target.wav, num)
    mixed_wav_path = formatter(dir_, hp.form.mixed.wav, num)
    #librosa.output.write_wav(target_wav_path, w1, srate)
    soundfile.write(target_wav_path, w1, srate)
    #librosa.output.write_wav(mixed_wav_path, mixed, srate)
    soundfile.write(mixed_wav_path, mixed, srate)

    upload_to_s3(target_wav_path)
    upload_to_s3(mixed_wav_path)

    # save magnitude spectrograms
    target_mag, _ = audio.wav2spec(w1)
    mixed_mag, _ = audio.wav2spec(mixed)
    target_mag_path = formatter(dir_, hp.form.target.mag, num)
    mixed_mag_path = formatter(dir_, hp.form.mixed.mag, num)
    torch.save(torch.from_numpy(target_mag), target_mag_path)
    torch.save(torch.from_numpy(mixed_mag), mixed_mag_path)

    # save selected sample as text file. d-vec will be calculated soon
    dvec_text_path = formatter(dir_, hp.form.dvec, num)
    with open(dvec_text_path, 'w') as f:
        f.write(s1_dvec)

    # TODO: delete the files created locally
    os.remove(target_wav_path)
    os.remove(mixed_wav_path)

def download_S3File(file_name, wav_key_name):
    #print('BUCKET_NAME', BUCKET_NAME)
    #print('wav_key_name', wav_key_name)
    #print('file_name', file_name)
    s3_client.download_file(BUCKET_NAME, wav_key_name, file_name)

def delete_local_file(file_name):
    os.remove(file_name)

def wav_files_names():
    wav_file_names = []
    wav_files = wav_file_keys_from_s3Bucket()
    for file_name in wav_files:
        separated = file_name.split('/')
        wav_file_names.append(separated[-1])
    return wav_file_names

def wav_file_name_and_paths():
    wav_file_keys = wav_file_keys_from_s3Bucket()
    wav_file_name_dict = {}
    for wav_file_key in wav_file_keys:
        wav_file_name = wav_file_key.split('/')[-1]
        wav_file_name_dict[wav_file_name] = wav_file_key
    return wav_file_name_dict


def wav_file_keys_from_s3Bucket(): 
    files = []
    objects = bucket.objects.all()
    for obj in objects:
        files.append(obj.key)
    wav_files = [f for f in files if 'wav' in f]
    return wav_files

def upload_to_s3(file_path):
    s3_client.upload_file(file_path, BUCKET_NAME, file_path)

def fileNameFromPath(file_path):
    return file_path.split('/')[-1]

def train_folders_from_S3():
    train_folders = [x for x in fs.glob('voicefilterdataset/train-clean-100/*')]
    return train_folders

def train_spk_from_s3_train_folders(train_folders):
    train_spk = [fs.glob(os.path.join(spk, '**', '*-norm.wav')) for spk in train_folders]
    return train_spk

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', type=str, required=True,
                        help="yaml file for configuration")
    parser.add_argument('-d', '--libri_dir', type=str, default=None,
                        help="Directory of LibriSpeech dataset, containing folders of train-clean-100, train-clean-360, dev-clean.")
    parser.add_argument('-v', '--voxceleb_dir', type=str, default=None,
                        help="Directory of VoxCeleb2 dataset, ends with 'aac'")
    parser.add_argument('-o', '--out_dir', type=str, required=True,
                        help="Directory of output training triplet")
    parser.add_argument('-p', '--process_num', type=int, default=None,
                        help='number of processes to run. default: cpu_count')
    parser.add_argument('--vad', type=int, default=0,
                        help='apply vad to wav file. yes(1) or no(0, default)')
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, 'train'), exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, 'test'), exist_ok=True)

    hp = HParam(args.config)

    cpu_num = cpu_count() if args.process_num is None else args.process_num
    '''
    if args.libri_dir is None and args.voxceleb_dir is None:
        raise Exception("Please provide directory of data")

    if args.libri_dir is not None:
        train_folders = [x for x in glob.glob(os.path.join(args.libri_dir, 'train-clean-100', '*'))
                            if os.path.isdir(x)] + \
                        [x for x in glob.glob(os.path.join(args.libri_dir, 'train-clean-360', '*'))
                            if os.path.isdir(x)]
                        # we recommned to exclude train-other-500
                        # See https://github.com/mindslab-ai/voicefilter/issues/5#issuecomment-497746793
                        # + \
                        #[x for x in glob.glob(os.path.join(args.libri_dir, 'train-other-500', '*'))
                        #    if os.path.isdir(x)]
        test_folders = [x for x in glob.glob(os.path.join(args.libri_dir, 'dev-clean', '*'))]

    elif args.voxceleb_dir is not None:
        all_folders = [x for x in glob.glob(os.path.join(args.voxceleb_dir, '*'))
                            if os.path.isdir(x)]
        train_folders = all_folders[:-20]
        test_folders = all_folders[-20:]

    train_spk = [glob.glob(os.path.join(spk, '**', hp.form.input), recursive=True)
                    for spk in train_folders]
    train_spk = [x for x in train_spk if len(x) >= 2]

    test_spk = [glob.glob(os.path.join(spk, '**', hp.form.input), recursive=True)
                    for spk in test_folders]
    test_spk = [x for x in test_spk if len(x) >= 2]
    '''

    train_folders = train_folders_from_S3()
    train_spk = train_spk_from_s3_train_folders(train_folders)

    audio = Audio(hp)


    def train_wrapper(num):
        wav_file_names = wav_files_names()
        wav_file_name_and_path_dict = wav_file_name_and_paths()
        spk1, spk2 = random.sample(train_spk, 2)
        #print(wav_file_names)
        #spk1 = random.sample(wav_file_names, 2)
        #print('spk1')
        #spk2 = random.sample(wav_file_names, 2)
        #print(spk1)
        s1_dvec, s1_target = random.sample(spk1, 2)
        #print('s1_dvec')
        #print(s1_dvec)
        s2 = random.choice(spk2)
        #TODO: make a dic that has the filename as key and wav_key_name as value 
        #print(s1_dvec)
        #print(s1_target)
        s1_dvec_file_name = fileNameFromPath(s1_dvec)
        s1_target_file_name = fileNameFromPath(s1_target)
        s2_file_name = fileNameFromPath(s2)
        download_S3File(s1_dvec_file_name, wav_file_name_and_path_dict[s1_dvec_file_name])
        download_S3File(s1_target_file_name, wav_file_name_and_path_dict[s1_target_file_name])
        download_S3File(s2_file_name, wav_file_name_and_path_dict[s2_file_name])
        mix(hp, args, audio, num, s1_dvec_file_name, s1_target_file_name, s2_file_name, train=True)

    def test_wrapper(num):
        spk1, spk2 = random.sample(test_spk, 2)
        s1_dvec, s1_target = random.sample(spk1, 2)
        s2 = random.choice(spk2)
        mix(hp, args, audio, num, s1_dvec, s1_target, s2, train=False)

    arr = list(range(10**5))
    with Pool(cpu_num) as p:
        r = list(tqdm.tqdm(p.imap(train_wrapper, arr), total=len(arr)))
'''
    arr = list(range(10**2))
    with Pool(cpu_num) as p:
        r = list(tqdm.tqdm(p.imap(test_wrapper, arr), total=len(arr)))
'''
