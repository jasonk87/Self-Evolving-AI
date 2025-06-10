import unittest
from game_logic import select_word, check_guess, determine_winner

class TestGameLogic(unittest.TestCase):

    def test_select_word(self):
        word_list = ["apple", "banana", "cherry"]
        word = select_word(word_list)
        self.assertIsInstance(word, str)
        self.assertTrue(len(word) > 0)
        self.assertTrue(word in word_list)

    def test_check_guess(self):
        word = "apple"
        guess = "a"
        result = check_guess(word, guess)
        self.assertEqual(result, True)

        guess = "z"
        result = check_guess(word, guess)
        self.assertEqual(result, False)

        word = "banana"
        guess = "n"
        result = check_guess(word, guess)
        self.assertEqual(result, True)

    def test_determine_winner(self):
        word = "apple"
        guessed_letters = ["a", "p", "p", "l", "e"]
        winner = determine_winner(word, guessed_letters)
        self.assertTrue(winner)

        word = "banana"
        guessed_letters = ["b", "a", "n", "a", "n", "a"]
        winner = determine_winner(word, guessed_letters)
        self.assertTrue(winner)

        word = "cherry"
        guessed_letters = ["c", "h", "e", "r", "r", "y"]
        winner = determine_winner(word, guessed_letters)
        self.assertTrue(winner)

if __name__ == '__main__':
    unittest.main()