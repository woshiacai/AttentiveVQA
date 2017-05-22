import argparse

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from torch.autograd import Variable 
from torch.nn.utils.rnn import pack_padded_sequence,pad_packed_sequence
from torchvision import transforms
from torchvision import models

cudnn.benchmark = True

import numpy as np
import os
from data_loader_coco import get_loader 
from build_vocab import Vocabulary
from models.encoder import EncoderCNN, EncoderRNN, EncoderFC, EncoderSkipThought
from models.classification_models import G_Spatial, MultimodalRNN
import pickle
import datetime

import json

from tensorboard_logger import configure, log_value

from tools.PythonHelperTools.vqaTools.vqa import VQA
from tools.PythonEvaluationTools.vqaEvaluation.vqaEval import VQAEval
import json
import random
import os

def run(save_path, args):
    torch.manual_seed(args.seed)

    # Create model directory
    if not os.path.exists(args.model_path):
        os.makedirs(args.model_path)
    
    # Image preprocessing
    train_transform = transforms.Compose([
        transforms.Scale((299,299)),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        ])

    test_transform = transforms.Compose([
        transforms.Scale((299,299)),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        ])
    
    # Load vocabulary wrapper.
    with open(args.question_vocab_path, 'rb') as f:
        question_vocab = pickle.load(f)

    with open(args.ans_vocab_path, 'rb') as f:
        ans_vocab = pickle.load(f)

    # with open("data/val_ans_vocab.pkl", 'rb') asf:
    #     val_ans_vocab = pickle.load(f)
    
    train_data_loader = get_loader("train", question_vocab, ans_vocab,
                             train_transform, args.batch_size,
                             shuffle=True, num_workers=args.num_workers)

    # test_data_loader = get_loader("test", question_vocab, ans_vocab,
    #                          train_transform, args.val_batch_size,
    #                          shuffle=False, num_workers=args.num_workers)

    val_data_loader = get_loader("val", question_vocab, ans_vocab,
                             train_transform, args.val_batch_size,
                             shuffle=False, num_workers=args.num_workers)

    # Build the models
    #encoder = EncoderCNN(args.embed_size,models.inception_v3(pretrained=True), requires_grad=False)
    encoder = None
    encoder_fc = None#EncoderFC(global_only=True)
    #netG = G_Spatial(args.embed_size, args.hidden_size, question_vocab, ans_vocab, args.num_layers)
    #netR = EncoderRNN(args.embed_size, args.hidden_size, len(question_vocab), args.num_layers)
    netR = EncoderSkipThought(question_vocab)
    netM = MultimodalRNN(len(ans_vocab))

    # if args.netG:
    #     print("[!]loading pretrained netG....")
    #     netG.load_state_dict(torch.load(args.netG))
    #     print("Done!")

    if args.encoder:
        print("[!]loading pretrained decoder....")
        encoder.load_state_dict(torch.load(args.encoder))
        print("Done!")

    criterion = nn.CrossEntropyLoss()
    
    states = (Variable(torch.zeros(args.num_layers, args.batch_size, args.hidden_size)),
     Variable(torch.zeros(args.num_layers, args.batch_size, args.hidden_size)))
    val_states = (Variable(torch.zeros(args.num_layers, args.val_batch_size, args.hidden_size)),
     Variable(torch.zeros(args.num_layers, args.val_batch_size, args.hidden_size)))

    y_onehot = torch.FloatTensor(args.batch_size, 20,len(question_vocab))

    if torch.cuda.is_available():
        #encoder = encoder.cuda()
        #netG.cuda()
        netR.cuda()
        netM.cuda()
        #encoder_fc.cuda()
        states = [s.cuda() for s in states]
        val_states = [s.cuda() for s in val_states]
        criterion = criterion.cuda()
        y_onehot = y_onehot.cuda()

    params = [
                {'params': netR.parameters()},
                {'params': netM.parameters()},
                #{'params': encoder_fc.parameters()}
                #{'params': encoder.parameters(), 'lr': 0.1 * args.learning_rate}
                #{'params': encoder.fc.parameters()}
                
            ]
    optimizer = torch.optim.Adam(params, lr=args.learning_rate,betas=(0.8, 0.999))

    # Train the Models
    total_step       = len(train_data_loader)
    total_iterations = 0

    for epoch in range(args.num_epochs):
        for i, (images, captions, lengths, ann_id, ans) in enumerate(train_data_loader):
            # Set mini-batch dataset
            images = Variable(images)
            captions = Variable(captions)
            ans = Variable(torch.LongTensor(ans))
            if torch.cuda.is_available():
                images = images.cuda()
                captions = captions.cuda()
                ans = ans.cuda()

            #ans, batch_sizes = pack_padded_sequence(ans, ans_lengths, batch_first=True)

            # y_onehot.resize_(captions.size(0),captions.size(1),len(question_vocab))
            # y_onehot.zero_()
            # y_onehot.scatter_(2,captions.data.unsqueeze(2),1)
            # y_v = Variable(y_onehot)

            netR.zero_grad()
            netM.zero_grad()
            #encoder_fc.zero_grad()
            #inputs = Variable(images.data, volatile=True)
            #features = Variable(encoder(inputs).data)
            visual_features = images
            text_features   = netR(captions, lengths)

            out = netM(visual_features, text_features)
            
            #out = netG(features=features, captions=y_v, lengths=lengths, states=states)

            mle_loss = criterion(out, ans)
            mle_loss.backward()
            #torch.nn.utils.clip_grad_norm(netG.parameters(), args.clip)
            optimizer.step()

            # Print log info
            if i % args.log_step == 0:
                print('Epoch [%d/%d], Step [%d/%d] Loss: %5.4f, Perplexity: %5.4f'
                      %(epoch, args.num_epochs, i, total_step,  mle_loss.data[0], np.exp(mle_loss.data[0])))

            # # Save the model
            # if (total_iterations+1) % args.save_step == 0:
            #     torch.save(netG.state_dict(), 
            #                os.path.join(save_path, 
            #                             'netG-%d-%d.pkl' %(epoch+1, i+1)))
            #     #torch.save(encoder.state_dict(), 
            #     #           os.path.join(save_path, 
            #     #                        'encoder-%d-%d.pkl' %(epoch+1, i+1)))


            if total_iterations % args.tb_log_step == 0:
                log_value('Loss', mle_loss.data[0], total_iterations)
                log_value('Perplexity', np.exp(mle_loss.data[0]), total_iterations)

            if (total_iterations+1) % args.save_step == 0:
                export(encoder, netR, netM, val_data_loader, y_onehot,
                 val_states, criterion, question_vocab,ans_vocab, total_iterations, total_step, save_path)

            total_iterations += 1

def export(encoder, netR, netM, data_loader,y_onehot, state, criterion,
    question_vocab,ans_vocab, total_iterations, total_step, save_path):

    for net in [netR, netM]:
        net.eval()
        for parameter in net.parameters():
            parameter.requires_grad = False

    responses = []
    for i, (images, captions, lengths, ann_id) in enumerate(data_loader):
        # Set mini-batch dataset
        images = Variable(images, volatile=True)
        captions = Variable(captions, volatile=True)
        if torch.cuda.is_available():
            images = images.cuda()
            captions = captions.cuda()

        visual_features = images #encoder_fc(images)
        text_features   = netR(captions, lengths)

        outputs = netM(visual_features, text_features)
        outputs = torch.max(outputs,1)[1]
        outputs = outputs.cpu().data.numpy().squeeze().tolist()

        for index in range(images.size(0)):
            answer = ans_vocab.idx2word[outputs[index]]
            responses.append({"answer":answer, "question_id": ann_id[index]})

        # Print log info
        if i % ( args.log_step * 10 ) == 0:
            print('Step [%d/%d] Exporting  ... '
                  %(i, len(data_loader)))

    json_save_dir = os.path.join(save_path, "{}_OpenEnded_mscoco_val2014_fake_results.json".format(total_iterations))
    json.dump(responses, open(json_save_dir, "w"))

    dataDir = 'data'
    taskType    ='OpenEnded'
    dataType    ='mscoco'  # 'mscoco' for real and 'abstract_v002' for abstract
    dataSubType ='val2014'
    annFile     ='%s/Annotations/v2_%s_%s_annotations.json'%(dataDir, dataType, dataSubType)
    quesFile    ='%s/Questions/v2_%s_%s_%s_questions.json'%(dataDir, taskType, dataType, dataSubType)
    imgDir      ='%s/Images/%s/%s/' %(dataDir, dataType, dataSubType)

    resFile = json_save_dir

    vqa = VQA(annFile, quesFile)
    vqaRes = vqa.loadRes(resFile, quesFile)
    vqaEval = VQAEval(vqa, vqaRes, n=2)
    vqaEval.evaluate()

    print "\n"
    print "Overall Accuracy is: %.02f\n" %(vqaEval.accuracy['overall'])
    log_value('Val_Acc', vqaEval.accuracy['overall'], total_iterations)

    for net in [netR, netM]:
        net.train()
        for parameter in net.parameters():
            parameter.requires_grad = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model_path', type=str, default='./models/' ,
                        help='path for saving trained models')
    parser.add_argument('--crop_size', type=int, default=299 ,
                        help='image size to use')
    parser.add_argument('--question_vocab_path', type=str, default='data/question_vocab.pkl',
                        help='path for vocabulary wrapper')
    parser.add_argument('--ans_vocab_path', type=str, default='data/ans_vocab.pkl',
                        help='path for vocabulary wrapper')
    parser.add_argument('--dataset', type=str, default='coco' ,
                        help='dataset to use')
    parser.add_argument('--comments_path', type=str,
                        default='data/labels.h5',
                        help='path for train annotation json file')
    parser.add_argument('--log_step', type=int , default=10,
                        help='step size for prining log info')
    parser.add_argument('--tb_log_step', type=int , default=100,
                        help='step size for prining log info')
    parser.add_argument('--save_step', type=int , default=10000,
                        help='step size for saving trained models')
    parser.add_argument('--val_step', type=int , default=10000,
                        help='step size for saving trained models')
    
    # Model parameters
    parser.add_argument('--embed_size', type=int , default=512 ,
                        help='dimension of word embedding vectors')
    parser.add_argument('--hidden_size', type=int , default=512 ,
                        help='dimension of gru hidden states')
    parser.add_argument('--num_layers', type=int , default=1 ,
                        help='number of layers in gru')
    parser.add_argument('--clip', type=float, default=0.25,
                    help='gradient clipping')
    parser.add_argument('--netG', type=str)
    parser.add_argument('--encoder', type=str)
    
    parser.add_argument('--num_epochs', type=int, default=500)
    parser.add_argument('--batch_size', type=int, default=200)
    parser.add_argument('--val_batch_size', type=int, default=256)
    parser.add_argument('--num_workers', type=int, default=8)
    parser.add_argument('--learning_rate', type=float, default=5e-4)
    parser.add_argument('--seed', type=int, default=123)
    args = parser.parse_args()
    print(args)
    if not os.path.exists("logs"):
        os.mkdir("logs")

    if not os.path.exists(os.path.join("logs", args.dataset)):
        os.mkdir(os.path.join("logs", args.dataset))

    now = datetime.datetime.now().strftime('%d%m%Y%H%M%S')
    save_path = os.path.join(os.path.join("logs", args.dataset), now)

    if not os.path.exists(save_path):
        os.mkdir(save_path)

    configure(save_path)
    run(save_path, args)