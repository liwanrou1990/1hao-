// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {MockERC20} from "./MockERC20.sol";

interface IFlashBorrower {
    /// @notice Called by the lender during a flash loan. Borrower must repay before returning.
    function onFlashLoan(address token, uint256 amount) external;
}

/// @title MockFlashLender
/// @notice Simulated ERC3156-style flash lender for the LOCAL TEST CHAIN only.
///         Models the economic primitive used in flash-loan governance attacks:
///         a borrower can hold a large token balance for the span of one transaction.
/// @dev    No real liquidity is at risk; this exists purely to prove whether a
///         governance contract is manipulable within a single block.
contract MockFlashLender {
    MockERC20 public immutable token;

    constructor(MockERC20 _token) {
        token = _token;
    }

    /// @notice Lend `amount` of `token` to msg.sender, call back, require full repayment.
    function flashLoan(uint256 amount) external {
        uint256 balBefore = token.balanceOf(address(this));
        require(balBefore >= amount, "lender: insufficient liquidity");

        token.transfer(msg.sender, amount);
        IFlashBorrower(msg.sender).onFlashLoan(address(token), amount);

        uint256 balAfter = token.balanceOf(address(this));
        require(balAfter >= balBefore, "lender: not repaid");
    }
}
