import os
import sys
import argparse
import tensorflow as tf
import numpy as np

from utils import to_categorical, get_conv_shape, get_comment_ids, find_newest_checkpoint
from input_handler import get_input_data_from_csv, get_input_data_from_text

from Layers import ConvBlockLayer

from keras.models import Model
from keras.layers.convolutional import Conv1D
from keras.layers.embeddings import Embedding
from keras.layers import Input, Dense, Dropout, Lambda
from keras.layers.pooling import MaxPooling1D
from keras.optimizers import SGD
from keras.callbacks import ModelCheckpoint


tf.logging.set_verbosity(tf.logging.INFO)
# Basic model parameters as external flags.
FLAGS = None
num_filters = [16, 32, 64, 128]


def build_model(num_filters, num_classes, sequence_max_length=128, num_quantized_chars=71, embedding_size=16, learning_rate=0.001, top_k=2, load_pretrained_model=False):
    inputs = Input(shape=(sequence_max_length, ), dtype='int32', name='inputs')
    embedded_sent = Embedding(num_quantized_chars, embedding_size, input_length=sequence_max_length)(inputs)

    # First conv layer
    conv = Conv1D(filters=8, kernel_size=2, strides=1, padding="same")(embedded_sent)

    # Each ConvBlock with one MaxPooling Layer
    for i in range(len(num_filters)):
        conv = ConvBlockLayer(get_conv_shape(conv), num_filters[i])(conv)
        conv = MaxPooling1D(pool_size=3, strides=2, padding="same")(conv)

    # k-max pooling (Finds values and indices of the k largest entries for the last dimension)
    def _top_k(x):
        x = tf.transpose(x, [0, 2, 1])
        k_max = tf.nn.top_k(x, k=top_k)
        return tf.reshape(k_max[0], (-1, num_filters[-1] * top_k))
    k_max = Lambda(_top_k, output_shape=(num_filters[-1] * top_k,))(conv)

    # 3 fully-connected layer with dropout regularization
    fc1 = Dropout(0.7)(Dense(128, activation='relu', kernel_initializer='he_normal')(k_max))
    fc2 = Dropout(0.7)(Dense(128, activation='relu', kernel_initializer='he_normal')(fc1))
    fc3 = Dense(num_classes, activation='softmax')(fc2)

    # define optimizer
    sgd = SGD(lr=learning_rate, decay=1e-6, momentum=0.9, nesterov=False)

    model = Model(inputs=inputs, outputs=fc3)
    model.compile(optimizer=sgd, loss='categorical_crossentropy', metrics=['accuracy'])

    if load_pretrained_model:
        if FLAGS.load_model is None:
            model.load_weights(find_newest_checkpoint(FLAGS.checkpoint_dir))
        else:
            model.load_weights(FLAGS.load_model)

    return model


def train_sentiment(input_file, max_feature_length, n_class, embedding_size, learning_rate, batch_size, num_epochs, save_dir=None, print_summary=False):
    # Stage 1: Convert raw texts into char-ids format && convert labels into one-hot vectors
    X_train, y_train_sentiment, _ = get_input_data_from_csv(input_file, max_feature_length)
    y_train_sentiment = to_categorical(y_train_sentiment, n_class)

    # Stage 2: Build Model
    model = build_model(num_filters=num_filters, num_classes=n_class, sequence_max_length=FLAGS.max_feature_length, embedding_size=embedding_size, learning_rate=learning_rate, load_pretrained_model=FLAGS.load_pretrain)

    # Stage 3: Training
    save_dir = save_dir if save_dir is not None else 'checkpoints'
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    filepath = os.path.join(save_dir, "weights-{epoch:02d}-{val_acc:.2f}.hdf5")
    checkpoint = ModelCheckpoint(filepath, monitor='val_acc', verbose=1, save_best_only=False, mode='max')

    if print_summary:
        print(model.summary())

    model.fit(
        x=X_train,
        y=y_train_sentiment,
        batch_size=batch_size,
        epochs=num_epochs,
        validation_split=0.33,
        callbacks=[checkpoint],
        shuffle=True,
        verbose=FLAGS.verbose
    )


def do_evaluation(eval_data, max_feature_length):
    if FLAGS.load_model is None:
        raise ValueError("You need to specify the model location by --load_model=[location]")

    # Load Testing Data
    comments = []
    sentiments = []
    comment_classes = []

    with open(eval_data, 'r') as f:
        for line in f.readlines():
            comment, sentiment, _class = line.split(',')
            comments.append(comment)
            sentiments.append(sentiment)
            comment_classes.append(_class)

    for i in xrange(len(comments)):
        X, y_sentiment, y_comment = get_input_data_from_text(comments[i], sentiments[i], comment_classes[i], max_feature_length)
        y_sentiment = to_categorical(y_sentiment, FLAGS.n_sentiment_classes)
        y_comment = to_categorical(y_comment, FLAGS.n_comment_classes)

        sentiment_model = build_model(num_filters=num_filters, num_classes=FLAGS.n_sentiment_classes, sequence_max_length=FLAGS.max_feature_length, embedding_size=FLAGS.embedding_size, learning_rate=FLAGS.learning_rate, load_pretrained_model=True)

        comment_model = build_model(num_filters=num_filters, num_classes=FLAGS.n_comment_classes, sequence_max_length=FLAGS.max_feature_length, embedding_size=FLAGS.embedding_size, learning_rate=FLAGS.learning_rate, load_pretrained_model=True)

        sentiment_loss_and_history = sentiment_model.evaluate(X, y_sentiment, batch_size=FLAGS.batch_size, verbose=1)
        comment_loss_and_history = comment_model.evaluate(X, y_sentiment, batch_size=FLAGS.batch_size, verbose=1)
        # print(sentiment_loss_and_history)
        # print("[*] ACCURACY OF TEST DATA: %.4f" % accuracy)


def do_sentiment_prediction(test_data, num_classes, max_feature_length):
    if FLAGS.load_model is None:
        raise ValueError("You need to specify the model location by --load_model=[location]")

    # Load Testing Data
    comments = []
    with open(test_data, 'r') as f:
        for comment in f.readlines():
            comments.append(get_comment_ids(comment, max_feature_length))

    X = np.asarray(comments, dtype='int32')
    print X.shape
    model = build_model(num_filters=num_filters, num_classes=num_classes, sequence_max_length=FLAGS.max_feature_length, embedding_size=FLAGS.embedding_size, learning_rate=FLAGS.learning_rate, load_pretrained_model=True)

    predictions = model.predict(X, batch_size=FLAGS.batch_size, verbose=1)


def run(_):
    if FLAGS.mode == 'train':
        train_sentiment(input_file=FLAGS.input_data, max_feature_length=FLAGS.max_feature_length, n_class=FLAGS.n_sentiment_classes, embedding_size=FLAGS.embedding_size, learning_rate=FLAGS.learning_rate, batch_size=FLAGS.batch_size, num_epochs=FLAGS.num_epochs, save_dir=FLAGS.checkpoint_dir, print_summary=FLAGS.print_summary)
    elif FLAGS.mode == 'eval':
        do_evaluation(FLAGS.test_data, FLAGS.max_feature_length)
    elif FLAGS.mode == 'pred':
        do_sentiment_prediction(FLAGS.test_data, FLAGS.n_sentiment_classes, FLAGS.max_feature_length)
    else:
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--n_sentiment_classes',
        type=int,
        default=3,
        help='Specify number of classes of sentiments'
    )
    parser.add_argument(
        '--n_comment_classes',
        type=int,
        default=10,
        help='Specify number of classes of comments'
    )
    parser.add_argument(
        '--num_epochs',
        type=int,
        default=10,
        help='Specify number of epochs'
    )
    parser.add_argument(
        '--batch_size',
        type=int,
        default=64,
        help='Batch size. Must divide evenly into the dataset sizes.'
    )
    parser.add_argument(
        '--learning_rate',
        type=float,
        default=0.01,
        help='Specify learning rate'
    )
    parser.add_argument(
        '--optimizer',
        type=str,
        default='sgd',
        help='Specify optimizer'
    )
    parser.add_argument(
        '--input_data',
        type=str,
        default='./data/train.csv',
        help='Location store the input data (only accept `csv` format)'  # [TODO: to support more data formats]
    )
    parser.add_argument(
        '--test_data',
        type=str,
        default='./data/test.txt',
        help='Specify test data path'
    )
    parser.add_argument(
        '--checkpoint_dir',
        type=str,
        default='checkpoints',
        help='Specify checkpoint directory'
    )
    parser.add_argument(
        '--output_dir',
        type=str,
        default='/Users/Michaeliu/Twitch/DCLCNN/TRAIN_MODEL',
        help='Directory to store the summaries and checkpoints.'
    )
    parser.add_argument(
        '--streamer',
        type=str,
        default='thijs',
        help='Specify a twitch streamer'
    )
    parser.add_argument(
        '--embedding_size',
        type=int,
        default=16,
        help='Specify embedding size'
    )
    parser.add_argument(
        '--max_feature_length',
        type=int,
        default=128,
        help='Specify max feature length'
    )
    parser.add_argument(
        '--evaluate_every',
        type=int,
        default=50,
        help='do evaluation after # numbers of training steps'
    )
    parser.add_argument(
        '--checkpoint_every',
        type=int,
        default=50,
        help='Save checkpoint after # numbers of training steps'
    )
    parser.add_argument(
        '--l2_weight_decay',
        type=float,
        default=1e-3,
        help='Specify max feature length'
    )
    parser.add_argument(
        '--mode',
        type=str,
        help='Specify mode: `train` or `eval` or `pred`',
        required=True
    )
    parser.add_argument(
        '--load_model',
        type=str,
        help='Specify the location of model weights',
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose on training',
        default=False
    )
    parser.add_argument(
        '--print_summary',
        action='store_true',
        help='Print out model summary',
        default=False
    )
    parser.add_argument(
        '--load_pretrain',
        action='store_true',
        help='Whether load pretrain model weights',
        default=False
    )

    FLAGS, unparsed = parser.parse_known_args()
    tf.app.run(main=run, argv=[sys.argv[0]] + unparsed)