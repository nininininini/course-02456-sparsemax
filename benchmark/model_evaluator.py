
import numpy as np
from scipy.stats import entropy
from sklearn.model_selection import StratifiedKFold, KFold


class ModelEvaluator:
    def __init__(self, model, dataset, epochs=1000,
                 random_state=None, verbose=False):
        self.model = model

        self.epochs = epochs
        self.random_state = random_state
        self.verbose = verbose

        # setup data
        self.x = dataset.inputs
        self.t = dataset.targets

    @staticmethod
    def evaluate(model, x_test, t_test):
        # prediction on test data
        target = t_test
        predict = model.predict(x_test)
        return np.mean(_jensen_shannon_divergence(predict, target))

    def single_fold(self, fold, train_index, test_index):
        # select datasets
        x_train = self.x[train_index, :]
        t_train = self.t[train_index, :]
        x_test = self.x[test_index, :]
        t_test = self.t[test_index, :]

        # reset model
        self.model.reset()

        # train model
        self.model.update(x_train, t_train, epochs=self.epochs)

        # prediction on test data
        divergence = self.evaluate(self.model, x_test, t_test)

        if self.verbose:
            print('      %d: %f' % (fold, divergence))

        # TODO: change this to a KL distance when a target distribution is used
        return divergence

    def all_folds(self, n_splits=5, stratified=True):
        # cross validation
        Splitter = StratifiedKFold if stratified else KFold
        skf = Splitter(
            n_splits=n_splits, shuffle=True, random_state=self.random_state
        )
        splits = skf.split(self.x, np.argmax(self.t, axis=1))

        # collect missrate data
        missrates = np.zeros(n_splits)
        for fold, (train_index, test_index) in enumerate(splits):
            # fit model and get final test performance
            missrates[fold] = self.single_fold(fold, train_index, test_index)

        return missrates


def _jensen_shannon_divergence(p, q):
    # p needs to be sparse, otherwise np.log(1e-1000) will be inf.
    p = p.astype(np.float64)
    p[p < 1e-30] = 0
    p /= np.sum(p, axis=1)[:, np.newaxis]

    m = 0.5 * (p.T + q.T)
    return 0.5 * (entropy(p.T, m) + entropy(q.T, m))
