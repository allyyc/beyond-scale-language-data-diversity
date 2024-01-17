"""
todo:
    - finish passing the HF block_size tokenization code here so its modular
    - add function to our train code train.py
    - print the sequence length of the data once we include this code
    - create a unit test here to test block size
    - use the re-init code smart ally & brando wrote
"""
from itertools import chain
import random

import torch

from transformers import PreTrainedTokenizer, AutoTokenizer, Trainer, TrainingArguments, AutoConfig
from transformers.testing_utils import CaptureLogger

def get_column_names(dataset, spit: str = 'train', method: str = 'features'):
    if method == 'features':
        column_names = next(iter(dataset)).keys()
    elif method == 'keys':
        column_names = list(dataset[spit].features)
    else:
        raise ValueError(f"method {method} not supported")
    return column_names

def get_data_from_hf_dataset(dataset, streaming: bool = True, batch_size: int = 4):
    """ Gets data from a HF dataset, it's usually an iterator object e.g., some ds.map(fn, batched=True, remove_columns=remove_columns) has been applied. 
    Handles both streaming and non-streaming datasets, take for streaming and select for non-streaming.
    """
    batch = dataset.take(batch_size) if streaming else dataset.select(random.sample(list(range(len(dataset))), batch_size))
    return batch

def _tokenize_function(examples, tokenizer, tok_logger, text_column_name: str):
    """
    
    To use do:
    tokenizer = ...obtained from your model... 
    tokenize_function = lambda examples: tokenize_function(examples, tokenizer=tokenizer) 
    tokenized_datasets = raw_datasets.map(
            tokenize_function,
            batched=True,
            remove_columns=column_names,
        )
    """
    with CaptureLogger(tok_logger) as cl:
        output = tokenizer(examples[text_column_name])
    # clm input could be much much longer than block_size
    if "Token indices sequence length is longer than the" in cl.out:
        tok_logger.warning(
            "^^^^^^^^^^^^^^^^ Please ignore the warning above - this long input will be chunked into smaller bits"
            " before being passed to the model."
        )
    return output

def tokenize_function(examples, tokenizer, text_column_name: str):
    """ 
    creates a tokenize function that can be used in HF's map function and you specify which text column to tokenize.
    
    Assumes batched=True so examples is many row/data points.
    """
    return tokenizer(examples["text_column_name"])

def group_texts(examples, block_size: int = 4096):
    """
    tokenizer = ...obtained from your model... 
    tokenize_function = lambda examples: tokenize_function(examples, tokenizer=tokenizer) 
    tokenized_datasets = raw_datasets.map(
            tokenize_function,
            batched=True,
            remove_columns=column_names,
        )

    # Note that with `batched=True`, this map processes 1,000 texts together, so group_texts throws away a remainder
    # for each of those groups of 1,000 texts. You can adjust that batch_size here but a higher value might be slower
    # to preprocess.
    #
    # To speed up this part, we use multiprocessing. See the documentation of the map method for more information:
    # https://huggingface.co/docs/datasets/package_reference/main_classes.html#datasets.Dataset.map    
    """
    # Concatenate all texts.
    concatenated_examples = {k: list(chain(*examples[k])) for k in examples.keys()}
    total_length = len(concatenated_examples[list(examples.keys())[0]])
    # We drop the small remainder, and if the total_length < block_size  we exclude this batch and return an empty dict.
    # We could add padding if the model supported it instead of this drop, you can customize this part to your needs.
    total_length = (total_length // block_size) * block_size
    # Split by chunks of max_len.
    result = {
        k: [t[i : i + block_size] for i in range(0, total_length, block_size)]
        for k, t in concatenated_examples.items()
    }
    result["labels"] = result["input_ids"].copy()
    return result

def collate_fn_train_only_first_eos_token_mask_everything_after_it(data: list[dict[str, str]], 
                                                                   tokenizer: PreTrainedTokenizer, 
                                                                   max_length: int=1024,  # GPT2 default, likely worth you change it! This default might cause bugs.
                                                                   ) -> dict[str, torch.Tensor]:
    """ Train only on first occurence of eos. The remaining eos are masked out.

    Sometimes the model might not have a padding token. Sometimes people set the padding token to be the eos token.
    But sometimes this seems to lead to the model to predict eos token to much. 
    So instead of actually using the pad token that was set to the eos token, we instead mask out all excesive eos tokens that act as pads 
    and leave the first eos token at the end to be predicted -- since that is the only one that semantically means end of sequence 
    and therby by not training on random eos at the end by masking it not unncesserily shift/amplify the distribution of eos. 
    
    ref: https://discuss.huggingface.co/t/why-does-the-falcon-qlora-tutorial-code-use-eos-token-as-pad-token/45954/13?u=brando 
    ref: https://chat.openai.com/share/02d16770-a1f3-4bf4-8fc2-464286daa8a1
    ref: https://claude.ai/chat/80565d1f-ece3-4fad-87df-364ce57aec15 on when to call .clone()
    ref: https://stackoverflow.com/questions/76633368/how-does-one-set-the-pad-token-correctly-not-to-eos-during-fine-tuning-to-avoi
    """
    # we are training full context length for llama so remove code bellow, if it tries to pad hopefully it throws an error
    # -- Ensure tokenizer has a padding token
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    # -- Extract sequences
    # sequences: list[str] = [example.get("text", "") or "" for example in data]
    sequences: list[str] = []
    for idx, example in enumerate(data):
        # Retrieve the value for "text" from the dictionary or default to an empty string if not present or falsy. ref: https://chat.openai.com/share/bead51fe-2acf-4f05-b8f7-b849134bbfd4
        text: str = example.get("text", "") or ""
        sequences.append(text)
    # -- Tokenize the sequences
    tokenized_data = tokenizer(sequences, padding="max_length", max_length=max_length, truncation=True, return_tensors="pt")
    tokenized_data["labels"] = tokenized_data["input_ids"].clone()  # labels is hardcoded in HF so put it!
    # -- Set the mask value for the first eos_token in each sequence to 1 and remaining to -100
    eos_token_id = tokenizer.eos_token_id
    for idx, input_ids in enumerate(tokenized_data["input_ids"]):
        # Find all occurrences of eos_token
        eos_positions = (input_ids == eos_token_id).nonzero(as_tuple=True)[0]
        if eos_positions.nelement() > 0:  # Check if eos_token is present
            first_eos_position = eos_positions[0]
            tokenized_data["attention_mask"][idx, first_eos_position] = 1  # Set the mask value to 1
            
            # Assert that the label for the first occurrence of eos_token is eos_token_id
            assert tokenized_data["labels"][idx, first_eos_position] == eos_token_id, "The label for the first eos_token is incorrect!"
            
            # For all subsequent occurrences of eos_token, set their labels to -100
            for subsequent_eos_position in eos_positions[1:]:
                tokenized_data["labels"][idx, subsequent_eos_position] = -100
                assert tokenized_data["labels"][idx, subsequent_eos_position] == -100, "The label for the subsequent_eos_position incorrect! Should be -100."
    return tokenized_data

# -- unit tests -- #

def _test_all_batches_are_size_block_size():
    batch_size = 4
    # get gpt2 tokenizer
    tokenizer = AutoTokenizer.from_pretrained("gpt2")
    tokenize_function = lambda examples: tokenizer(examples["text"])
    # load c4 data set hf in streaming mode 
    from datasets import load_dataset
    streaming = True
    raw_datasets = load_dataset("c4", "en", streaming=streaming)
    remove_columns = get_column_names(raw_datasets)  # remove all keys that are not tensors to avoid bugs in collate function in task2vec's pytorch data loader

    # how does it know which column to tokenize? gpt4 says default is text or your tokenized function can specify it, see my lambda fun above
    tokenized_datasets = raw_datasets.map(
        tokenize_function,
        batched=True,  # Setting `batched=True` in the `dataset.map` function of Hugging Face's datasets library processes the data in batches rather than one item at a time, significantly speeding up the tokenization and preprocessing steps.
        remove_columns=remove_columns,
    )

    lm_datasets = tokenized_datasets.map(
        group_texts,
        batched=True,
    )

if __name__ == "__main__":
    pass