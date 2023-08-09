import time

from diversity.task2vec import Task2Vec 
from diversity import task_similarity

from pathlib import Path

import torch
import torch.nn as nn

# def alginment_with_diversity_coefficient(dataset_target,
#                                         dataset_source,
#                                         get_mapped_batch_fn: callable, 
#                                         probe_network: nn.Module,
#                                         tokenizer = None,
#                                         batch_size: int = 512,
#                                         num_batches: int = 100, 
#                                         seed = 0, 
#                                         buffer_size: int = 500_000, 
#                                         distance = 'cosine',
#                           ) -> dict:
#     """
#     Alignment v1 - with the Diversity Coefficient
    
#     Given two data sets, compute how aligned they are using probe network f_w by comparing batches across the data sets:
#         alg1 = align(T, S, f_w) = Align_1(T, S, f_w) = E_{B_s ~ S, B_t ~ T} [1 - d(e_{B_s}, e_{B_t})] =  1 - div(T, S)
#     where e_{D} is the Task2Vec (diagonal of FIM) embedding of a batch D, and d is cosine distance function.
    
#     ref: https://arxiv.org/abs/2306.13840
#     """
#     results: dict = cross_diversity_coefficient(dataset_target, dataset_source, get_mapped_batch_fn, probe_network, tokenizer, batch_size, num_batches, seed, buffer_size, distance)
#     results['align'] = 1 - results['div_coeff']
#     results['align_ci'] = results['div_coeff_ci']
#     return results


def alignment_task2vec(dataset_target,
                        dataset_source,
                        map_target: callable,
                        map_source: callable,
                        probe_network: nn.Module,
                        tokenizer = None,
                        batch_size: int = 1024,
                        seed = 0, 
                        buffer_size: int = 500_000, 
                        distance = 'cosine',
                        verbose: bool = False,
                        debug: bool = False,
                        ) -> dict:
    """
    Alignment v2 - with Task2Vec

    Given two data sets, compute how aligned they are using probe network f_w 
        alg_2 = Align_2(T, S, f_w) = 1 - d(e_{D_S}, e_{D_T})
    by comparing embedding the entire dataset or a large batch. 

    Note: there is no sense of number of batches here, so num_batches = 1 effectively + if CIs needed need to be with wrt batch examples. 
    """
    # - Get target shuffled data
    shuffled_dataset = dataset_target.shuffle(buffer_size=buffer_size, seed=seed)
    # raw_text_batch = shuffled_dataset.take(batch_size)
    raw_text_batch = dataset_target.take(batch_size)
    tokenized_batch = map_target(raw_text_batch)
    if verbose:
        print(f'{raw_text_batch=}')
        print(f'{tokenized_batch=}')
        # time_start = time.time()
        # really slow with suffle it seems
        # print(f'{next(iter(raw_text_batch))=}')
        # print(f'{next(iter(tokenized_batch))=}')
        # print(f'Time it took: {time.time() - time_start} seconds \a\n')
    
    # - Get Task2Vec embedding for batch
    if not debug:
        embedding_target, loss_target = Task2Vec(probe_network).embed(tokenized_batch)
    else:
        embedding_target, loss_target = Task2Vec(probe_network, classifier_opts={'break_early': True}).embed(tokenized_batch, epochs=1)  # only for debugging
    print(f'{loss_target=}\n{embedding_target=}\n') if verbose else None

    # - Get source shuffled data
    shuffled_dataset = dataset_source.shuffle(buffer_size=buffer_size, seed=seed)
    # raw_text_batch = shuffled_dataset.take(batch_size)
    raw_text_batch = dataset_target.take(batch_size)
    tokenized_batch = map_source(raw_text_batch)
    
    # - Get Task2Vec embedding for batch
    if not debug:
        embedding_source, loss_source = Task2Vec(probe_network).embed(tokenized_batch)
    else:
        embedding_source, loss_source = Task2Vec(probe_network, classifier_opts={'break_early': True}).embed(tokenized_batch, epochs=1)  # only for debugging
    print(f'{loss_target=}\n{embedding_target=}\n') if verbose else None

    # - Compute alignment
    distance_matrix = task_similarity.pdist([embedding_target, embedding_source], distance=distance)
    align = 1 - distance_matrix[0, 1]
    align_ci = task_similarity.stats_of_distance_matrix(distance_matrix)[1]

    # - Compute results
    embeddings, losses = [], []
    embeddings.append({'embedding_target': embedding_target, 'embedding_source': embedding_source})
    losses.append({'loss_target': loss_target, 'loss_source': loss_source})

    # - Results
    results: dict = {'align': align, 'align_ci': align_ci,
                    'embeddings': embeddings,
                    'distance_matrix': distance_matrix,
                    'losses': losses,
                    "batch_size": batch_size}
    return results
    
# - Tests, examples

def test_get_batch_from_dataset():
    batch_size = 10
    token = None

    from transformers import GPT2Tokenizer, GPT2LMHeadModel
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
    if tokenizer.pad_token_id is None:
      tokenizer.pad_token = tokenizer.eos_token

    # -- Get batch from dataset
    from datasets import load_dataset
    # path, name = 'brando/debug0_autoformalization', 'debug0_autoformalization'
    # https://huggingface.co/datasets/brando/debug1_af
    path, name = 'brando/debug1_af', 'debug1_af'
    # path, name = 'c4', 'en'
    # path, name = "wikitext", 'wikitext-103-v1'
    # path, name = Path('~/data-quality/debug_data/debug_data_15_examples_round_trip/RoundTripNthPowersData_Sheet1.csv').expanduser(), None
    dataset = load_dataset(path, name, streaming=True, split="train").with_format("torch")

    # - Gets a raw text batch
    batch = dataset.take(batch_size)
    print(f'{batch=}')
    print(f'{next(iter(batch))=}')
    print(f'{list(batch)[0]=}')

    # - Tokenize text batch
    def preprocess(examples):
        return tokenizer(examples["informal"], padding="max_length", max_length=128, truncation=True, return_tensors="pt")
    batch = batch.map(preprocess, batched=True, remove_columns=[])
    print(f'{batch=}')
    print(f'{next(iter(batch))=}')
    print()

# - Experiments

def sanity2_af_is_aligned_to_af():
    """ Sanity check that data from the same place has low. Prev work showed 0.05 is lower bound.
    so hopefully around that number. """
    batch_size = 512
    remove_columns = []
    # token = keys=$(cat ~/data/openai_api_key.txt)
    token = open(Path('~/data/hf_token.txt').expanduser()).read().strip()
    # print(f'{token=}')  # CAREFUL PRINTING THIS AND PUSHING TO GITHUB, WANDB etc.

    # -- Get probe network
    from datasets import load_dataset
    import torch
    from transformers import GPT2Tokenizer, GPT2LMHeadModel
    
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
    if tokenizer.pad_token_id is None:
      tokenizer.pad_token = tokenizer.eos_token
    probe_network = GPT2LMHeadModel.from_pretrained("gpt2")
    device = torch.device(f"cuda:{0}" if torch.cuda.is_available() else "cpu")
    probe_network = probe_network.to(device)

    # -- Get batch from dataset
    from datasets import load_dataset
    # path, name = 'brando/debug0_af', 'debug0_af'
    # path, name = 'brando/debug1_af', 'debug1_af'
    # remove_columns = []
    path, name = 'c4', 'en'  # sanity check, this should 1. run code 2. have high alignment
    remove_columns = ["text", "timestamp", "url"]
    # path, name = "wikitext", 'wikitext-103-v1'
    # path, name = Path('~/data-quality/debug_data/debug_data_15_examples_round_trip/RoundTripNthPowersData_Sheet1.csv').expanduser(), None
    dataset = load_dataset(path, name, streaming=True, split="train", token=token).with_format("torch")
    print(f'{dataset=}')
    batch = dataset.take(batch_size)
    print(f'{next(iter(batch))=}')

    # - Prepare functions to tokenize batch
    def preprocess(examples):  # gets the raw text batch according to the specific names in table in data set & tokenize
        return tokenizer(examples["text"], padding="max_length", max_length=128, truncation=True, return_tensors="pt")
    # def preprocess(examples):  # gets the raw text batch according to the specific names in table in data set & tokenize
    #     return tokenizer(examples["informal"], padding="max_length", max_length=128, truncation=True, return_tensors="pt")
    def map(batch):  # apply preprocess to batch to all examples in batch represented as a dataset
        return batch.map(preprocess, batched=True, remove_columns=remove_columns)
    tokenized_batch = batch.map(preprocess, batched=True, remove_columns=remove_columns)
    tokenized_batch = map(batch)
    print(f'{next(iter(tokenized_batch))=}')

    # -- Compute alignment
    print('-- Compute alignment...')
    # results = alignment_task2vec(dataset, dataset, map, map, probe_network, batch_size=8)
    results = alignment_task2vec(dataset, dataset, map, map, probe_network, verbose=True, debug=True, batch_size=8)
    print(f'{results=}')


if __name__ == '__main__':
    print(f'\n\n\n------------------- Running {__file__} -------------------')
    # -- Run tests and time it
    import time
    time_start = time.time()
    # -- Run tests
    # test_get_batch_from_dataset()
    sanity2_af_is_aligned_to_af()
    # -- End tests, report how long it took
    print(f'Time it took: {time.time() - time_start} seconds \a\n')