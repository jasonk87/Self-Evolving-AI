def infer_argument_value(arg_name: str, context: dict, default: Any = None) -> Any:
    """
    Infers the value of an argument by checking a context dictionary first, then using a default value.
    
    Parameters:
        arg_name (str): The name of the argument to infer.
        context (dict): A dictionary containing potential values for the argument.
        default (Any, optional): The default value to use if the argument is not found in the context.
    
    Returns:
        Any: The inferred value of the argument.
    
    Raises:
        ValueError: If the argument is not found in the context and no default is provided.
    """
    try:
        if arg_name in context:
            return context[arg_name]
        if default is not None:
            return default
        raise ValueError(f"Argument '{arg_name}' not found in context and no default provided.")
    except Exception as e:
        raise type(e)(f"Error inferring argument '{arg_name}': {str(e)}") from e